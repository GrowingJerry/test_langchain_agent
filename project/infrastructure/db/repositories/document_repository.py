"""Document and project visual-asset persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from infrastructure.db.repositories.base import BaseRepository, new_id, now_iso


class DocumentRepository(BaseRepository):
    def add(
        self, project_id: str, filename: str, file_type: str, file_path: Path
    ) -> str:
        document_id = new_id("DOC")
        with self.connections.transaction() as conn:
            conn.execute(
                "INSERT INTO project_documents VALUES (?, ?, ?, ?, ?, ?)",
                (
                    document_id,
                    project_id,
                    filename,
                    file_type,
                    str(file_path),
                    now_iso(),
                ),
            )
            conn.execute(
                "UPDATE projects SET updated_at = ? WHERE project_id = ?",
                (now_iso(), project_id),
            )
        return document_id

    def list(self, project_id: str) -> List[Dict[str, Any]]:
        with self.connections.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_documents WHERE project_id = ? ORDER BY created_at DESC",
                    (project_id,),
                )
            ]

    def delete(self, project_id: str, document_id: str) -> None:
        with self.connections.transaction() as conn:
            conn.execute(
                "DELETE FROM project_chunk_embeddings WHERE project_id = ? AND document_id = ?",
                (project_id, document_id),
            )
            conn.execute(
                "DELETE FROM project_chunks WHERE project_id = ? AND document_id = ?",
                (project_id, document_id),
            )
            conn.execute(
                "DELETE FROM project_documents WHERE project_id = ? AND document_id = ?",
                (project_id, document_id),
            )

    def save_asset(self, project_id: str, data: Dict[str, Any]) -> str:
        asset_id = str(data.get("asset_id") or "").strip() or new_id("AST")
        with self.connections.transaction() as conn:
            existing = conn.execute(
                "SELECT project_id FROM project_assets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
            if existing and existing["project_id"] != project_id:
                raise ValueError("asset_id already belongs to another project")
            conn.execute(
                """INSERT INTO project_assets VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(asset_id) DO UPDATE SET project_id=excluded.project_id, document_id=excluded.document_id,
                asset_type=excluded.asset_type, file_path=excluded.file_path, page_no=excluded.page_no,
                source_document=excluded.source_document""",
                (
                    asset_id,
                    project_id,
                    str(data.get("document_id") or "") or None,
                    str(data.get("asset_type") or ""),
                    str(data.get("file_path") or ""),
                    data.get("page_no")
                    if isinstance(data.get("page_no"), int)
                    else None,
                    str(data.get("source_document") or ""),
                    str(data.get("created_at") or now_iso()),
                ),
            )
        return asset_id

    def list_assets(self, project_id: str) -> List[Dict[str, Any]]:
        with self.connections.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_assets WHERE project_id = ? ORDER BY created_at DESC, asset_id",
                    (project_id,),
                )
            ]

    def save_evidence(
        self, project_id: str, asset_id: str, data: Dict[str, Any]
    ) -> str:
        evidence_id = str(data.get("evidence_id") or "").strip() or new_id("EVD")
        with self.connections.transaction() as conn:
            if not conn.execute(
                "SELECT 1 FROM project_assets WHERE project_id = ? AND asset_id = ?",
                (project_id, asset_id),
            ).fetchone():
                raise ValueError("asset_id does not belong to the current project")
            existing = conn.execute(
                "SELECT project_id, asset_id FROM project_visual_evidence WHERE evidence_id = ?",
                (evidence_id,),
            ).fetchone()
            if existing and (
                existing["project_id"] != project_id or existing["asset_id"] != asset_id
            ):
                raise ValueError("evidence_id already belongs to another project asset")
            payload = (
                data.get("evidence") if isinstance(data.get("evidence"), dict) else data
            )
            conn.execute(
                """INSERT INTO project_visual_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_id) DO UPDATE SET evidence_type=excluded.evidence_type,
                evidence_json=excluded.evidence_json, visible_text=excluded.visible_text,
                source_region=excluded.source_region, confidence=excluded.confidence,
                need_human_confirm=excluded.need_human_confirm, raw_response=excluded.raw_response""",
                (
                    evidence_id,
                    project_id,
                    asset_id,
                    str(
                        data.get("evidence_type") or payload.get("evidence_type") or ""
                    ),
                    json.dumps(payload, ensure_ascii=False),
                    str(data.get("visible_text") or payload.get("visible_text") or ""),
                    json.dumps(
                        data.get("source_region") or payload.get("source_region") or {},
                        ensure_ascii=False,
                    ),
                    float(data.get("confidence") or payload.get("confidence") or 0),
                    int(
                        bool(
                            data.get(
                                "need_human_confirm",
                                payload.get("need_human_confirm", True),
                            )
                        )
                    ),
                    str(data.get("raw_response") or ""),
                    now_iso(),
                ),
            )
        return evidence_id

    @staticmethod
    def _decode(row: Any) -> Dict[str, Any]:
        item = dict(row)
        for source, target, default in (
            ("evidence_json", "evidence", {}),
            ("source_region", "source_region", {}),
        ):
            try:
                item[target] = json.loads(item.get(source) or json.dumps(default))
            except json.JSONDecodeError:
                item[target] = default
        item["need_human_confirm"] = bool(item.get("need_human_confirm"))
        return item

    def list_evidence(
        self, project_id: str, asset_id: str = ""
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM project_visual_evidence WHERE project_id = ?"
        params: tuple[Any, ...] = (project_id,)
        if asset_id:
            query += " AND asset_id = ?"
            params = (project_id, asset_id)
        query += " ORDER BY created_at DESC, evidence_id"
        with self.connections.connection() as conn:
            return [self._decode(row) for row in conn.execute(query, params)]
