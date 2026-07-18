"""Project-scoped scenario-card persistence."""

import json
from typing import Any, Dict, List, Optional

from infrastructure.db.repositories.base import BaseRepository, new_id, now_iso


class ScenarioRepository(BaseRepository):
    LIST_FIELDS = (
        "related_requirements",
        "actors",
        "preconditions",
        "input_data",
        "external_interfaces",
        "environment",
        "normal_flow",
        "abnormal_flow",
        "boundary_conditions",
        "performance_constraints",
        "safety_constraints",
        "source_document",
        "source_chunk_ids",
    )

    def replace_basic(
        self, project_id: str, requirements: List[Dict[str, Any]]
    ) -> None:
        with self.connections.transaction() as conn:
            conn.execute("DELETE FROM scenario_cards WHERE project_id=?", (project_id,))
            for row in requirements:
                description = str(row.get("description") or row.get("title") or "")
                conn.execute(
                    """INSERT INTO scenario_cards(scenario_id,project_id,requirement_id,title,preconditions,actions,expected_outcome,source_chunk_id,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        new_id("SCN"),
                        project_id,
                        row.get("requirement_id", ""),
                        row.get("title", ""),
                        "按当前项目文档准备测试环境、对象和输入条件",
                        description,
                        "实际结果应符合需求描述；未给出阈值时需人工确认",
                        row.get("source_chunk_id", ""),
                        now_iso(),
                    ),
                )

    def replace_full(self, project_id: str, cards: List[Dict[str, Any]]) -> None:
        with self.connections.transaction() as conn:
            conn.execute("DELETE FROM scenario_cards WHERE project_id=?", (project_id,))
            for card in cards:
                payload = dict(card)
                payload["project_id"] = project_id
                values = {
                    key: json.dumps(payload.get(key, []), ensure_ascii=False)
                    for key in self.LIST_FIELDS
                }
                related = payload.get("related_requirements") or [""]
                chunks = payload.get("source_chunk_ids") or [""]
                conn.execute(
                    """INSERT INTO scenario_cards(scenario_id,project_id,requirement_id,title,preconditions,actions,expected_outcome,source_chunk_id,created_at,
                    scenario_name,scenario_type,related_requirements,actors,trigger_event,input_data,system_state,external_interfaces,environment,normal_flow,abnormal_flow,
                    boundary_conditions,performance_constraints,safety_constraints,source_document,source_chunk_ids,confidence,need_human_confirm,card_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        payload.get("scenario_id") or new_id("SCN"),
                        project_id,
                        related[0],
                        payload.get("scenario_name", ""),
                        values["preconditions"],
                        values["normal_flow"],
                        "按需求文档规定值判定",
                        chunks[0],
                        now_iso(),
                        payload.get("scenario_name", ""),
                        payload.get("scenario_type", "业务场景"),
                        values["related_requirements"],
                        values["actors"],
                        payload.get("trigger_event", ""),
                        values["input_data"],
                        payload.get("system_state", ""),
                        values["external_interfaces"],
                        values["environment"],
                        values["normal_flow"],
                        values["abnormal_flow"],
                        values["boundary_conditions"],
                        values["performance_constraints"],
                        values["safety_constraints"],
                        values["source_document"],
                        values["source_chunk_ids"],
                        float(payload.get("confidence") or 0),
                        int(bool(payload.get("need_human_confirm", True))),
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )

    def get_for_requirement(
        self, project_id: str, requirement_id: str
    ) -> Optional[Dict[str, Any]]:
        with self.connections.connection() as conn:
            row = conn.execute(
                "SELECT * FROM scenario_cards WHERE project_id=? AND requirement_id=? LIMIT 1",
                (project_id, requirement_id),
            ).fetchone()
            return dict(row) if row else None

    def list(self, project_id: str, requirement_id: str = "") -> List[Dict[str, Any]]:
        with self.connections.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM scenario_cards WHERE project_id=? ORDER BY created_at,scenario_id",
                (project_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                card = json.loads(item.get("card_json") or "{}")
            except json.JSONDecodeError:
                card = {}
            if not card:
                card = {
                    "scenario_id": item.get("scenario_id"),
                    "project_id": project_id,
                    "scenario_name": item.get("scenario_name") or item.get("title"),
                    "related_requirements": [item.get("requirement_id")]
                    if item.get("requirement_id")
                    else [],
                }
            if not requirement_id or requirement_id in (
                card.get("related_requirements") or []
            ):
                result.append(card)
        return result
