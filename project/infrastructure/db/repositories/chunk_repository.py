"""Project chunk and embedding persistence."""

from __future__ import annotations

from typing import Any, Dict, List

from infrastructure.db.json_codec import dumps_json, loads_json
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
                    for key in (
                        "page_no",
                        "source_type",
                        "page_start",
                        "page_end",
                        "parser_type",
                        "chunk_level",
                        "parent_section_id",
                        "needs_ocr",
                        "parse_warning",
                        "section_title",
                        "child_index",
                        "table_titles",
                        "figure_titles",
                        "equation_numbers",
                    ):
                        metadata.setdefault(key, chunk.get(key))
                else:
                    content = str(getattr(chunk, "text", None) or chunk or "")
                    metadata = getattr(chunk, "metadata", {}) or {}
                conn.execute(
                    """INSERT INTO project_chunks(
                    chunk_id,project_id,document_id,chunk_index,content,created_at,
                    page_no,source_type,chunk_text,page_start,page_end,parser_type,
                    chunk_level,parent_section_id,needs_ocr,parse_warning,metadata_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                        metadata.get("page_start", metadata.get("page_no")),
                        metadata.get("page_end", metadata.get("page_no")),
                        metadata.get("parser_type", ""),
                        metadata.get("chunk_level", "legacy"),
                        metadata.get("parent_section_id", ""),
                        int(bool(metadata.get("needs_ocr"))),
                        metadata.get("parse_warning", ""),
                        dumps_json(
                            {
                                key: value
                                for key, value in metadata.items()
                                if key
                                not in {
                                    "page_no",
                                    "source_type",
                                    "page_start",
                                    "page_end",
                                    "parser_type",
                                    "chunk_level",
                                    "parent_section_id",
                                    "needs_ocr",
                                    "parse_warning",
                                }
                            }
                        ),
                    ),
                )
            conn.execute(
                "UPDATE projects SET updated_at=? WHERE project_id=?",
                (now_iso(), project_id),
            )
        return ids

    def append(
        self,
        project_id: str,
        document_id: str,
        chunks: List[Any],
        parse_report: Dict[str, Any] | None = None,
        processing_status: str = "processing",
    ) -> List[str]:
        """Append one completed parsing checkpoint without replacing earlier pages."""
        ids: List[str] = []
        with self.connections.transaction() as conn:
            row = conn.execute(
                """SELECT COALESCE(MAX(chunk_index),-1) FROM project_chunks
                WHERE project_id=? AND document_id=?""",
                (project_id, document_id),
            ).fetchone()
            start_index = int(row[0]) + 1
            for offset, chunk in enumerate(chunks):
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
                    for key in (
                        "page_no",
                        "source_type",
                        "page_start",
                        "page_end",
                        "parser_type",
                        "chunk_level",
                        "parent_section_id",
                        "needs_ocr",
                        "parse_warning",
                        "section_title",
                        "child_index",
                        "table_titles",
                        "figure_titles",
                        "equation_numbers",
                    ):
                        metadata.setdefault(key, chunk.get(key))
                else:
                    content = str(getattr(chunk, "text", None) or chunk or "")
                    metadata = getattr(chunk, "metadata", {}) or {}
                conn.execute(
                    """INSERT INTO project_chunks(
                    chunk_id,project_id,document_id,chunk_index,content,created_at,
                    page_no,source_type,chunk_text,page_start,page_end,parser_type,
                    chunk_level,parent_section_id,needs_ocr,parse_warning,metadata_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        chunk_id,
                        project_id,
                        document_id,
                        start_index + offset,
                        content,
                        now_iso(),
                        metadata.get("page_no"),
                        metadata.get("source_type", "text"),
                        content,
                        metadata.get("page_start", metadata.get("page_no")),
                        metadata.get("page_end", metadata.get("page_no")),
                        metadata.get("parser_type", ""),
                        metadata.get("chunk_level", "legacy"),
                        metadata.get("parent_section_id", ""),
                        int(bool(metadata.get("needs_ocr"))),
                        metadata.get("parse_warning", ""),
                        dumps_json(
                            {
                                key: value
                                for key, value in metadata.items()
                                if key
                                not in {
                                    "page_no",
                                    "source_type",
                                    "page_start",
                                    "page_end",
                                    "parser_type",
                                    "chunk_level",
                                    "parent_section_id",
                                    "needs_ocr",
                                    "parse_warning",
                                }
                            }
                        ),
                    ),
                )
            conn.execute(
                "UPDATE projects SET updated_at=? WHERE project_id=?",
                (now_iso(), project_id),
            )
            if parse_report is not None:
                conn.execute(
                    """UPDATE project_documents SET parser_type=?,parse_report_json=?,
                    processing_status=?,last_processed_page=?
                    WHERE project_id=? AND document_id=?""",
                    (
                        str(parse_report.get("parser_type") or ""),
                        dumps_json(parse_report),
                        processing_status,
                        int(parse_report.get("last_completed_page") or 0),
                        project_id,
                        document_id,
                    ),
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
                    dumps_json(embedding),
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
                item["embedding"] = loads_json(item.get("embedding_json"), [])
                result.append(item)
            return result
