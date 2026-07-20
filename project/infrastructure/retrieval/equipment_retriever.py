"""Project-scoped equipment candidate retrieval with optional embedding supplementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from infrastructure.db.repositories.equipment_repository import EquipmentRepository
from infrastructure.retrieval.embeddings import (
    EmbeddingProvider,
    OllamaEmbeddingProvider,
)
from infrastructure.retrieval.hybrid_search import vector_score


@dataclass
class EquipmentCandidateResult:
    candidates: List[Dict[str, Any]]
    used_embedding: bool = False


class EquipmentRetriever:
    """Retrieve candidates; it never makes the final applicability decision."""

    def __init__(
        self,
        repository: EquipmentRepository,
        embedding_provider: Optional[EmbeddingProvider] = None,
    ) -> None:
        self.repository = repository
        self.embedding_provider = embedding_provider

    def retrieve(
        self,
        project_id: str,
        *,
        name: str = "",
        category: str = "",
        description: str = "",
        allow_global: bool = False,
        limit: int = 100,
    ) -> EquipmentCandidateResult:
        if name.strip():
            candidates = self.repository.search_by_name_or_alias(
                project_id, name, allow_global, limit
            )
        elif category.strip():
            candidates = self.repository.list_by_category(
                project_id, category, allow_global, limit
            )
        else:
            candidates = self.repository.list_candidates(
                project_id, allow_global, limit
            )
        candidates = self._prefer_project_entities(candidates, project_id)
        if not description.strip() or name.strip() or not candidates:
            return EquipmentCandidateResult(candidates=candidates)

        provider = self.embedding_provider or OllamaEmbeddingProvider()
        query_embedding = provider.embed(description)
        if query_embedding is None:
            return EquipmentCandidateResult(candidates=candidates)
        used_embedding = False
        for candidate in candidates:
            candidate_text = " ".join(
                part
                for part in (
                    str(candidate.get("name") or ""),
                    str(candidate.get("category") or ""),
                    str(candidate.get("platform_type") or ""),
                    str(candidate.get("source_description") or ""),
                )
                if part
            )
            candidate_embedding = provider.embed(candidate_text)
            semantic = (
                vector_score(query_embedding, candidate_embedding)
                if candidate_embedding is not None
                else 0.0
            )
            candidate["_semantic_score"] = semantic
            used_embedding = used_embedding or candidate_embedding is not None
        candidates.sort(
            key=lambda row: (
                -float(row.get("_semantic_score") or 0),
                0 if row.get("project_id") == project_id else 1,
                str(row.get("name") or ""),
            )
        )
        return EquipmentCandidateResult(
            candidates=candidates, used_embedding=used_embedding
        )

    @staticmethod
    def _prefer_project_entities(
        candidates: List[Dict[str, Any]], project_id: str
    ) -> List[Dict[str, Any]]:
        """When names collide, a current-project entity shadows its GLOBAL counterpart."""
        ordered = sorted(
            candidates,
            key=lambda row: (
                0 if row.get("project_id") == project_id else 1,
                str(row.get("name") or ""),
            ),
        )
        result: List[Dict[str, Any]] = []
        seen_names: set[str] = set()
        for row in ordered:
            key = str(row.get("name") or "").strip().casefold()
            if key and key in seen_names:
                continue
            if key:
                seen_names.add(key)
            result.append(row)
        return result

