"""Project-bound retrieval over SQLite chunks with hybrid local scoring."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from domain.exceptions import ProjectScopeError, RetrievalError
from domain.schemas.retrieval import ProjectChunkResult
from infrastructure.retrieval.embeddings import (
    EmbeddingProvider,
    OllamaEmbeddingProvider,
)
from infrastructure.retrieval.hybrid_search import (
    hybrid_score,
    keyword_score,
    tokenize_query,
    vector_score,
)


class ProjectRetriever:
    """Retriever permanently bound to one project ID by its service layer."""

    def __init__(
        self,
        manager: Any,
        project_id: str,
        embedding_provider: Optional[EmbeddingProvider] = None,
    ) -> None:
        bound_project_id = str(project_id or "").strip()
        if not bound_project_id:
            raise ProjectScopeError(
                "A non-empty project_id must be bound when creating a retriever"
            )
        self.manager = manager
        self.project_id = bound_project_id
        self.embedding_provider = embedding_provider or OllamaEmbeddingProvider()

    def ensure_embeddings(self, chunks: Optional[List[Dict[str, Any]]] = None) -> int:
        """Persist missing embeddings for chunks that pass project-scope validation."""
        rows = (
            chunks
            if chunks is not None
            else self.manager.list_chunks(self.project_id, limit=5000)
        )
        self._validate_scope(rows)
        existing = {
            str(row.get("chunk_id"))
            for row in self.manager.list_chunk_embeddings(
                self.project_id, self.embedding_provider.model
            )
            if row.get("chunk_id")
        }
        count = 0
        for row in rows:
            chunk_id = str(row.get("chunk_id") or "")
            if not chunk_id or chunk_id in existing:
                continue
            content = str(row.get("content") or row.get("chunk_text") or "").strip()
            embedding = self.embedding_provider.embed(content)
            if embedding is None:
                break
            self.manager.save_chunk_embedding(
                project_id=self.project_id,
                document_id=str(row.get("document_id") or ""),
                chunk_id=chunk_id,
                embedding=embedding,
                model_name=self.embedding_provider.model,
            )
            existing.add(chunk_id)
            count += 1
        return count

    def search(self, query: str, top_k: int = 5) -> List[ProjectChunkResult]:
        """Return deduplicated, source-preserving results for the bound project only."""
        if top_k <= 0:
            return []
        source = (query or "").strip()
        tokens = tokenize_query(source)
        if not source:
            return []
        try:
            rows = self.manager.list_chunks(self.project_id, limit=2000)
            self._validate_scope(rows)
            if not rows:
                return []
            query_embedding = self.embedding_provider.embed(source)
            embedding_rows: Dict[str, List[float]] = {}
            if query_embedding is not None:
                self.ensure_embeddings(rows)
                stored = self.manager.list_chunk_embeddings(
                    self.project_id, self.embedding_provider.model
                )
                self._validate_scope(stored)
                embedding_rows = {
                    str(row.get("chunk_id")): list(row.get("embedding") or [])
                    for row in stored
                    if row.get("chunk_id")
                }
            unique: Dict[str, ProjectChunkResult] = {}
            for row in rows:
                result = self._score_row(row, tokens, query_embedding, embedding_rows)
                if result is None:
                    continue
                existing = unique.get(result.chunk_id)
                if existing is None or result.score > existing.score:
                    unique[result.chunk_id] = result
            ranked = sorted(
                unique.values(), key=lambda item: (-item.score, item.chunk_id)
            )
            self._validate_result_scope(ranked)
            return ranked[:top_k]
        except ProjectScopeError:
            raise
        except (ValueError, TypeError, OSError) as exc:
            raise RetrievalError(
                f"Project retrieval failed for {self.project_id}: {exc!r}"
            ) from exc

    def _score_row(
        self,
        row: Dict[str, Any],
        tokens: List[str],
        query_embedding: Optional[List[float]],
        embeddings: Dict[str, List[float]],
    ) -> Optional[ProjectChunkResult]:
        content = str(row.get("content") or row.get("chunk_text") or "")
        lexical = keyword_score(content, tokens)
        chunk_embedding = embeddings.get(str(row.get("chunk_id") or ""), [])
        semantic = (
            vector_score(query_embedding or [], chunk_embedding)
            if query_embedding is not None
            else 0.0
        )
        has_embedding = query_embedding is not None and bool(chunk_embedding)
        if lexical <= 0 and semantic <= 0:
            return None
        method = (
            "hybrid"
            if lexical > 0 and semantic > 0
            else ("embedding" if semantic > 0 else "keyword")
        )
        final = hybrid_score(lexical, semantic, has_embedding)
        return ProjectChunkResult(
            chunk_id=str(row.get("chunk_id") or ""),
            project_id=self.project_id,
            document_id=str(row.get("document_id") or ""),
            document_name=str(row.get("filename") or row.get("document_name") or ""),
            content=content,
            score=round(final, 4),
            retrieval_method=method,
            page_number=row.get("page_no")
            if isinstance(row.get("page_no"), int)
            else None,
            position_metadata={
                "chunk_index": row.get("chunk_index"),
                "source_type": str(row.get("source_type") or ""),
            },
            keyword_score=round(lexical, 4),
            vector_score=round(semantic, 4),
        )

    def _validate_scope(self, rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            row_project_id = str(row.get("project_id") or "")
            if row_project_id != self.project_id:
                raise ProjectScopeError(
                    f"Retrieved row project_id={row_project_id!r} does not match bound project {self.project_id!r}"
                )

    def _validate_result_scope(self, rows: List[ProjectChunkResult]) -> None:
        if any(row.project_id != self.project_id for row in rows):
            raise ProjectScopeError(
                "A retrieval result escaped the bound project scope"
            )
