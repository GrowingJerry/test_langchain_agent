"""Stable project-scoped case identifiers assigned after model validation."""
from __future__ import annotations
import re
from hashlib import sha256
from typing import Any

IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")

class CaseIdService:
    def __init__(self, manager: Any, settings: Any) -> None:
        self.manager, self.settings = manager, settings

    def prefix_for(self, project_id: str, requirement_id: str) -> tuple[str, str]:
        with self.manager.connections.connection() as conn:
            row = conn.execute("SELECT node_id,parent_id,identifier,ancestor_identifiers_json FROM requirement_nodes WHERE project_id=? AND identifier=?", (project_id, requirement_id)).fetchone()
            parent = None
            if row and row[1]:
                parent = conn.execute("SELECT identifier FROM requirement_nodes WHERE project_id=? AND node_id=?", (project_id, row[1])).fetchone()
        current = str(row[2] if row else requirement_id).upper().strip("[]")
        parent_id = str(parent[0]).upper().strip("[]") if parent else ""
        if parent_id and IDENTIFIER.match(parent_id) and current.startswith(parent_id + "_"):
            return parent_id, current[len(parent_id) + 1:]
        if IDENTIFIER.match(current):
            parts = current.split("_")
            return ("_".join(parts[:-1]), parts[-1]) if len(parts) > 1 else (current, current)
        stable = sha256(f"{project_id}:{requirement_id}".encode()).hexdigest()[:8].upper()
        return self.settings.case_id_fallback_prefix, stable

    def assign(self, project_id: str, requirement_id: str, cases: list[Any]) -> list[Any]:
        prefix, module = self.prefix_for(project_id, requirement_id)
        stem = f"{prefix}-{module}-"
        existing = [str(row.get("case_id") or "") for row in self.manager.list_generated_cases(project_id)]
        used = [int(value[len(stem):]) for value in existing if value.startswith(stem) and value[len(stem):].isdigit()]
        sequence = max(used, default=0) + 1
        assigned = []
        for case in cases:
            case_id = f"{stem}{sequence:0{self.settings.case_id_sequence_width}d}"
            assigned.append(case.model_copy(update={"case_id": case_id}))
            sequence += 1
        return assigned
