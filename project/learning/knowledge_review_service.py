"""Human review workflow, audit trail, and scenario-safe knowledge queries."""

from __future__ import annotations

from typing import Any, Dict, List

from infrastructure.db.json_codec import dumps_json, loads_json
from infrastructure.db.repositories.base import new_id, now_iso
from learning.knowledge_conflict_detector import knowledge_priority

KNOWLEDGE_STATUSES = {"draft", "reviewed", "approved", "rejected", "deprecated"}


class KnowledgeReviewService:
    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def review(self, project_id: str, knowledge_unit_id: str, *, new_status: str,
               reviewer: str, comments: List[str] | None = None,
               corrections: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if new_status not in KNOWLEDGE_STATUSES:
            raise ValueError(f"不支持的知识状态：{new_status}")
        if not reviewer.strip():
            raise ValueError("审核人不能为空")
        timestamp = now_iso()
        review_id = new_id("KRV")
        with self.manager.connections.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_units WHERE project_id=? AND knowledge_unit_id=?",
                (project_id, knowledge_unit_id),
            ).fetchone()
            if not row:
                raise ValueError("知识不存在或不属于当前项目")
            previous = str(row["status"])
            normalized = loads_json(row["normalized_data_json"], {})
            if corrections:
                normalized.update(corrections)
            conn.execute(
                """UPDATE knowledge_units SET status=?,normalized_data_json=?,
                need_human_confirm=?,approved_by=?,approved_at=?,updated_at=?
                WHERE project_id=? AND knowledge_unit_id=?""",
                (new_status, dumps_json(normalized), int(new_status == "draft"),
                 reviewer if new_status == "approved" else row["approved_by"],
                 timestamp if new_status == "approved" else row["approved_at"],
                 timestamp, project_id, knowledge_unit_id),
            )
            audit = {"knowledge_unit_id": knowledge_unit_id, "previous_status": previous,
                     "new_status": new_status, "reviewer": reviewer,
                     "comments": comments or [], "corrections": corrections or {}}
            conn.execute(
                """INSERT INTO knowledge_reviews(review_id,project_id,knowledge_unit_id,
                status,reviewer,comments_json,corrections_json,document_id,chunk_id,page_no,
                reviewed_at,previous_status,new_status,action,audit_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (review_id, project_id, knowledge_unit_id, new_status, reviewer,
                 dumps_json(comments or []), dumps_json(corrections or {}),
                 row["document_id"], row["chunk_id"], row["page_no"], timestamp,
                 previous, new_status, "status_change", dumps_json(audit)),
            )
        return audit | {"review_id": review_id}

    def list_audit_log(self, project_id: str, knowledge_unit_id: str = "") -> List[Dict[str, Any]]:
        query = "SELECT * FROM knowledge_reviews WHERE project_id=?"
        params: tuple[Any, ...] = (project_id,)
        if knowledge_unit_id:
            query += " AND knowledge_unit_id=?"
            params += (knowledge_unit_id,)
        query += " ORDER BY reviewed_at,review_id"
        with self.manager.connections.connection() as conn:
            return [dict(row) | {"audit": loads_json(row["audit_json"], {})} for row in conn.execute(query, params)]

    def query_for_scenario(self, project_id: str, *, section_scope: str = "",
                           include_global: bool = False) -> List[Dict[str, Any]]:
        projects = [project_id, *( ["GLOBAL"] if include_global else [])]
        placeholders = ",".join("?" for _ in projects)
        with self.manager.connections.connection() as conn:
            rows = conn.execute(
                f"""SELECT * FROM knowledge_units WHERE project_id IN ({placeholders})
                AND (status='approved' OR (status='reviewed' AND knowledge_type!='parameter'))""",
                tuple(projects),
            )
            result = []
            for raw in rows:
                row = dict(raw)
                if section_scope and row.get("section_scope") not in {"", section_scope}:
                    continue
                row["normalized_data"] = loads_json(row.get("normalized_data_json"), {})
                row["applicable_conditions"] = loads_json(row.get("applicable_conditions_json"), [])
                row["inapplicable_conditions"] = loads_json(row.get("inapplicable_conditions_json"), [])
                row["effective_priority"] = knowledge_priority(row, project_id)
                result.append(row)
        return sorted(result, key=lambda row: (-row["effective_priority"], row["knowledge_unit_id"]))

    def list_knowledge(
        self, project_id: str, *, status: str = "", knowledge_type: str = "",
        source_kind: str = "", only_conflicts: bool = False,
    ) -> List[Dict[str, Any]]:
        query = "SELECT * FROM knowledge_units WHERE project_id=?"
        params: list[Any] = [project_id]
        for column, value in (("status", status), ("knowledge_type", knowledge_type),
                              ("source_kind", source_kind)):
            if value:
                query += f" AND {column}=?"
                params.append(value)
        if only_conflicts:
            query += " AND knowledge_unit_id IN (SELECT value FROM knowledge_conflicts,json_each(knowledge_unit_ids_json) WHERE knowledge_conflicts.project_id=?)"
            params.append(project_id)
        query += " ORDER BY updated_at DESC,knowledge_unit_id"
        with self.manager.connections.connection() as conn:
            rows = []
            for raw in conn.execute(query, tuple(params)):
                row = dict(raw)
                row["normalized_data"] = loads_json(row.get("normalized_data_json"), {})
                row["tags"] = loads_json(row.get("tags_json"), [])
                row["applicable_conditions"] = loads_json(row.get("applicable_conditions_json"), [])
                row["inapplicable_conditions"] = loads_json(row.get("inapplicable_conditions_json"), [])
                rows.append(row)
            return rows

    def list_conflicts(self, project_id: str, status: str = "") -> List[Dict[str, Any]]:
        query = "SELECT * FROM knowledge_conflicts WHERE project_id=?"
        params: tuple[Any, ...] = (project_id,)
        if status:
            query += " AND status=?"
            params += (status,)
        query += " ORDER BY updated_at DESC,conflict_id"
        with self.manager.connections.connection() as conn:
            return [dict(row) | {
                "knowledge_unit_ids": loads_json(row["knowledge_unit_ids_json"], [])
            } for row in conn.execute(query, params)]
