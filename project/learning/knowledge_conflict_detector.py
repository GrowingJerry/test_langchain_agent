"""Deterministic knowledge conflict detection; conflicting versions are retained."""

from __future__ import annotations

import hashlib
from itertools import combinations
from typing import Any, Dict, List

from infrastructure.db.json_codec import dumps_json, loads_json
from infrastructure.db.repositories.base import new_id, now_iso

CONFLICT_TYPES = {
    "same_parameter_different_value", "same_symbol_different_meaning",
    "missing_unit", "incompatible_operating_condition",
    "project_document_vs_book", "approved_rule_vs_new_candidate",
    "duplicate_knowledge", "missing_source",
}

SOURCE_PRIORITY = {
    "formal_document": 500,
    "human_feedback": 400,
    "organization_template": 300,
    "book": 200,
    "historical_case": 100,
}


def knowledge_priority(row: Dict[str, Any], project_id: str) -> int:
    if row.get("project_id") == project_id and row.get("status") == "approved":
        return 600
    return SOURCE_PRIORITY.get(str(row.get("source_kind") or "book"), 0)


class KnowledgeConflictDetector:
    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def detect(self, project_id: str) -> List[Dict[str, Any]]:
        units = self._load_units(project_id)
        findings: List[tuple[str, List[Dict[str, Any]], str]] = []
        for unit in units:
            data = unit["normalized_data"]
            if not unit.get("document_id") and not unit.get("chunk_id"):
                findings.append(("missing_source", [unit], "知识缺少文档或片段来源"))
            if unit.get("knowledge_type") == "parameter" and data.get("value") is not None and not data.get("unit"):
                findings.append(("missing_unit", [unit], "数值参数缺少单位"))
        for left, right in combinations(units, 2):
            if not self._comparable(left, right, project_id):
                continue
            findings.extend(self._pair_conflicts(left, right))
        return [self._persist(project_id, kind, rows, message) for kind, rows, message in findings]

    def _load_units(self, project_id: str) -> List[Dict[str, Any]]:
        with self.manager.connections.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_units WHERE project_id IN (?, 'GLOBAL') AND status!='deprecated'",
                (project_id,),
            )
            result = []
            for raw in rows:
                row = dict(raw)
                row["normalized_data"] = loads_json(row.get("normalized_data_json"), {})
                result.append(row)
            return result

    @staticmethod
    def _comparable(left: Dict[str, Any], right: Dict[str, Any], project_id: str) -> bool:
        return bool(
            left["knowledge_unit_id"] != right["knowledge_unit_id"]
            and left.get("project_id") in {project_id, "GLOBAL"}
            and right.get("project_id") in {project_id, "GLOBAL"}
        )

    def _pair_conflicts(self, left: Dict[str, Any], right: Dict[str, Any]) -> List[tuple[str, List[Dict[str, Any]], str]]:
        findings = []
        ld, rd = left["normalized_data"], right["normalized_data"]
        same_scope = (left.get("section_scope") or "") == (right.get("section_scope") or "")
        same_title = str(left.get("title") or "").strip().lower() == str(right.get("title") or "").strip().lower()
        if same_title and str(left.get("content") or "").strip() == str(right.get("content") or "").strip():
            findings.append(("duplicate_knowledge", [left, right], "标题和内容相同"))
        if left.get("knowledge_type") == right.get("knowledge_type") == "parameter" and same_scope:
            lname, rname = str(ld.get("name") or left.get("title")), str(rd.get("name") or right.get("title"))
            if lname == rname and ld.get("value") != rd.get("value"):
                findings.append(("same_parameter_different_value", [left, right], f"参数 {lname} 的值不同"))
            if ld.get("symbol") and ld.get("symbol") == rd.get("symbol") and lname != rname:
                findings.append(("same_symbol_different_meaning", [left, right], f"符号 {ld['symbol']} 在同一作用域含义不同"))
            if lname == rname and ld.get("operating_condition") and rd.get("operating_condition") and ld.get("operating_condition") != rd.get("operating_condition"):
                findings.append(("incompatible_operating_condition", [left, right], f"参数 {lname} 的运行条件不兼容"))
        kinds = {left.get("source_kind"), right.get("source_kind")}
        if same_title and kinds == {"formal_document", "book"} and left.get("content") != right.get("content"):
            findings.append(("project_document_vs_book", [left, right], "项目正式文档与通用书籍内容不同"))
        statuses = {left.get("status"), right.get("status")}
        if same_title and "approved" in statuses and statuses & {"draft", "reviewed"} and left.get("content") != right.get("content"):
            findings.append(("approved_rule_vs_new_candidate", [left, right], "已批准知识与新候选不一致"))
        return findings

    def _persist(self, project_id: str, kind: str, units: List[Dict[str, Any]], description: str) -> Dict[str, Any]:
        ids = sorted(str(row["knowledge_unit_id"]) for row in units)
        key = hashlib.sha256(f"{project_id}|{kind}|{'|'.join(ids)}".encode()).hexdigest()
        winner = max(units, key=lambda row: knowledge_priority(row, project_id))
        conflict_id = new_id("KCF")
        first = units[0]
        with self.manager.connections.transaction() as conn:
            conn.execute(
                """INSERT INTO knowledge_conflicts(conflict_id,project_id,
                knowledge_unit_ids_json,conflict_type,description,status,resolution,
                document_id,chunk_id,page_no,created_at,updated_at,conflict_key,priority_winner_id)
                VALUES(?,?,?,?,?,'open','',?,?,?,?,?,?,?)
                ON CONFLICT(conflict_key) WHERE conflict_key IS NOT NULL DO UPDATE SET
                description=excluded.description,
                priority_winner_id=excluded.priority_winner_id,
                updated_at=excluded.updated_at""",
                (conflict_id, project_id, dumps_json(ids), kind, description,
                 first.get("document_id"), first.get("chunk_id"), first.get("page_no"),
                 now_iso(), now_iso(), key, winner["knowledge_unit_id"]),
            )
            row = conn.execute("SELECT * FROM knowledge_conflicts WHERE conflict_key=?", (key,)).fetchone()
        result = dict(row)
        result["knowledge_unit_ids"] = loads_json(result.get("knowledge_unit_ids_json"), [])
        return result
