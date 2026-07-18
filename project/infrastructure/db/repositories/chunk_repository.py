"""Project chunk and embedding persistence."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from infrastructure.db.repositories.base import BaseRepository, new_id, now_iso


class ChunkRepository(BaseRepository):
    def replace(
        self, project_id: str, document_id: str, chunks: List[Any]
    ) -> List[str]:
        ids: List[str] = []
        with self.connections.transaction() as conn:
            conn.execute(
                "DELETE FROM project_chunk_embeddings WHERE project_id=? AND document_id=?",
                (project_id, document_id),
            )
            conn.execute(
                "DELETE FROM project_chunks WHERE project_id=? AND document_id=?",
                (project_id, document_id),
            )
            for index, chunk in enumerate(chunks):
                chunk_id = new_id("CHK")
                ids.append(chunk_id)
                if isinstance(chunk, dict):
                    content = str(
                        chunk.get("chunk_text")
                        or chunk.get("content")
                        or chunk.get("text")
                        or ""
                    )
                    metadata = dict(chunk.get("metadata") or {})
                    metadata.setdefault("page_no", chunk.get("page_no"))
                    metadata.setdefault("source_type", chunk.get("source_type"))
                else:
                    content = str(getattr(chunk, "text", None) or chunk or "")
                    metadata = getattr(chunk, "metadata", {}) or {}
                conn.execute(
                    """INSERT INTO project_chunks(chunk_id,project_id,document_id,chunk_index,content,created_at,page_no,source_type,chunk_text)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        chunk_id,
                        project_id,
                        document_id,
                        index,
                        content,
                        now_iso(),
                        metadata.get("page_no"),
                        metadata.get("source_type", "text"),
                        content,
                    ),
                )
            conn.execute(
                "UPDATE projects SET updated_at=? WHERE project_id=?",
                (now_iso(), project_id),
            )
        return ids

    def list(self, project_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        with self.connections.connection() as conn:
            rows = conn.execute(
                """SELECT c.*, d.filename FROM project_chunks c JOIN project_documents d
                ON c.document_id=d.document_id WHERE c.project_id=? ORDER BY c.created_at,c.chunk_index LIMIT ?""",
                (project_id, limit),
            )
            return [dict(row) for row in rows]

    def combined_text(self, project_id: str, max_chars: int = 24000) -> str:
        return "\n\n".join(
            str(row.get("content") or row.get("chunk_text") or "")
            for row in self.list(project_id, 5000)
        )[:max_chars]

    def save_embedding(
        self,
        project_id: str,
        document_id: str,
        chunk_id: str,
        embedding: List[float],
        model_name: str,
    ) -> None:
        with self.connections.transaction() as conn:
            conn.execute(
                """INSERT INTO project_chunk_embeddings VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(project_id,chunk_id,model_name) DO UPDATE SET embedding_json=excluded.embedding_json,created_at=excluded.created_at""",
                (
                    new_id("EMB"),
                    project_id,
                    document_id,
                    chunk_id,
                    json.dumps(embedding),
                    model_name,
                    now_iso(),
                ),
            )

    def list_embeddings(
        self, project_id: str, model_name: str = ""
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM project_chunk_embeddings WHERE project_id=?"
        params: tuple[Any, ...] = (project_id,)
        if model_name:
            query += " AND model_name=?"
            params = (project_id, model_name)
        with self.connections.connection() as conn:
            result = []
            for row in conn.execute(query, params):
                item = dict(row)
                try:
                    item["embedding"] = json.loads(item.get("embedding_json") or "[]")
                except json.JSONDecodeError:
                    item["embedding"] = []
                result.append(item)
            return result
