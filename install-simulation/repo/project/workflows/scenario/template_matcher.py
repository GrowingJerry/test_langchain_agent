"""Approved-template matching without model decisions."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from domain.schemas.scenario_spec import ScenarioIntent
from infrastructure.database.json_codec import loads_json
from infrastructure.retrieval.hybrid_search import keyword_score, tokenize_query


class TemplateMatcher:
    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def match(self, intent: ScenarioIntent) -> Optional[Dict[str, Any]]:
        scopes = [intent.project_id]
        if intent.use_project_defaults:
            scopes.append("GLOBAL")
        placeholders = ",".join("?" for _ in scopes)
        with self.manager.connections.connection() as conn:
            rows = [dict(row) for row in conn.execute(
                f"SELECT * FROM scenario_templates WHERE project_id IN ({placeholders}) AND status='approved'",
                tuple(scopes),
            )]
        tokens = tokenize_query(" ".join([intent.effective_goal, intent.target_subsystem,
                                           intent.mission_phase, *intent.focus_risks]))
        ranked: List[tuple[float, int, str, Dict[str, Any]]] = []
        for row in rows:
            payload = loads_json(row.get("template_json"), {})
            text = " ".join([str(row.get("name") or ""), str(row.get("scenario_category") or ""),
                             str(payload.get("description") or ""), " ".join(payload.get("tags") or [])])
            score = keyword_score(text, tokens)
            if score > 0:
                row["template"] = payload
                ranked.append((score, 0 if row["project_id"] == intent.project_id else 1,
                               str(row["template_id"]), row))
        return sorted(ranked, key=lambda item: (-item[0], item[1], item[2]))[0][3] if ranked else None
