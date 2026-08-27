"""Project-scoped persistence for traceability artifacts and case versions."""

from __future__ import annotations

import json
from typing import Any

from infrastructure.repositories.base import BaseRepository
from domain.case_schema import merge_case, normalize_case, validate_case


class TraceabilityRepository(BaseRepository):
    def create_case_version(self, project_id: str, case_id: str, case: dict[str, Any], *,
                            feedback: str = "", context: dict[str, Any] | None = None,
                            model_name: str = "", operator: str = "") -> dict[str, Any]:
        if not project_id or not case_id:
            raise ValueError("project_id and case_id are required")
        with self.connections.transaction() as conn:
            formal = conn.execute(
                "SELECT case_json FROM generated_cases WHERE project_id=? AND case_id=?",
                (project_id, case_id),
            ).fetchone()
            original = json.loads(formal[0]) if formal and formal[0] else {}
            case = merge_case(original, case, feedback) if original else normalize_case(case)
            case.setdefault("case_id", case_id); case.setdefault("project_id", project_id)
            case = validate_case(case, case_id=case_id)
            row = conn.execute(
                "SELECT version_no, case_json FROM case_versions WHERE project_id=? AND case_id=? ORDER BY version_no DESC LIMIT 1",
                (project_id, case_id),
            ).fetchone()
            parent = int(row[0]) if row else None
            previous = json.loads(row[1]) if row else {}
            version = (parent or 0) + 1
            changed = sorted(k for k in set(previous) | set(case) if previous.get(k) != case.get(k))
            conn.execute(
                "INSERT INTO case_versions(project_id,case_id,version_no,parent_version_no,case_json,user_feedback,context_snapshot_json,model_name,changed_fields_json,operator) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (project_id, case_id, version, parent, json.dumps(case, ensure_ascii=False), feedback,
                 json.dumps(context or {}, ensure_ascii=False), model_name, json.dumps(changed, ensure_ascii=False), operator),
            )
        return {"project_id": project_id, "case_id": case_id, "version_no": version,
                "parent_version_no": parent, "changed_fields": changed, "acceptance_status": "proposed"}

    def list_case_versions(self, project_id: str, case_id: str) -> list[dict[str, Any]]:
        with self.connections.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM case_versions WHERE project_id=? AND case_id=? ORDER BY version_no",
                (project_id, case_id),
            ).fetchall()
        result=[]
        for row in rows:
            item=dict(row); item["case_json"]=json.loads(item["case_json"]); item["changed_fields"]=json.loads(item["changed_fields_json"])
            result.append(item)
        return result

    def set_version_status(self, project_id: str, case_id: str, version_no: int, status: str) -> None:
        if status not in {"proposed", "accepted", "rejected", "rolled_back"}:
            raise ValueError("invalid version status")
        with self.connections.transaction() as conn:
            cursor=conn.execute("UPDATE case_versions SET acceptance_status=? WHERE project_id=? AND case_id=? AND version_no=?",
                                (status, project_id, case_id, version_no))
            if cursor.rowcount != 1: raise KeyError("case version not found in current project")

    def accept_version(self, project_id: str, case_id: str, version_no: int) -> dict[str, Any]:
        with self.connections.transaction() as conn:
            row=conn.execute("SELECT * FROM case_versions WHERE project_id=? AND case_id=? AND version_no=?",(project_id,case_id,version_no)).fetchone()
            if not row: raise KeyError("case version not found in current project")
            selected=dict(row); selected["case_json"]=validate_case(json.loads(selected["case_json"]),case_id=case_id)
            conn.execute("UPDATE case_versions SET acceptance_status='rejected' WHERE project_id=? AND case_id=? AND acceptance_status='accepted'",(project_id,case_id))
            status=conn.execute("UPDATE case_versions SET acceptance_status='accepted' WHERE project_id=? AND case_id=? AND version_no=?",(project_id,case_id,version_no))
            updated=conn.execute("UPDATE generated_cases SET case_json=? WHERE project_id=? AND case_id=?",(json.dumps(selected["case_json"],ensure_ascii=False),project_id,case_id))
            if status.rowcount != 1 or updated.rowcount != 1: raise KeyError("formal generated case not found in current project")
            saved=conn.execute("SELECT case_json FROM generated_cases WHERE project_id=? AND case_id=?",(project_id,case_id)).fetchone()
            if validate_case(json.loads(saved[0]),case_id=case_id) != selected["case_json"]: raise ValueError("接受版本与正式用例不一致")
        return selected

    def rollback(self, project_id: str, case_id: str, version_no: int, operator: str = "") -> dict[str, Any]:
        selected=next((x for x in self.list_case_versions(project_id,case_id) if x["version_no"]==version_no),None)
        if not selected: raise KeyError("case version not found in current project")
        created=self.create_case_version(project_id,case_id,selected["case_json"],feedback=f"回滚到版本{version_no}",context={"rollback_from":version_no},operator=operator)
        self.accept_version(project_id,case_id,created["version_no"]); return created
