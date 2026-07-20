"""Project-scoped scenario-card persistence."""

from typing import Any, Dict, List, Optional

from infrastructure.db.json_codec import dumps_json, loads_json
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
                    key: dumps_json(payload.get(key, []))
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
                        dumps_json(payload),
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
            card = loads_json(item.get("card_json"), {})
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

    def save_compilation(
        self,
        project_id: str,
        scenarios: List[Dict[str, Any]],
        validations: List[Dict[str, Any]],
        *,
        input_data: Dict[str, Any],
        template_id: str = "",
        provenance: Dict[str, Any],
        step_trace: List[Dict[str, Any]],
    ) -> str:
        """Append one compilation run and its artifacts without replacing old scenarios."""
        run_id = new_id("SGR")
        timestamp = now_iso()
        with self.connections.transaction() as conn:
            for scenario in scenarios:
                conn.execute(
                    """INSERT INTO scenario_cards(
                    scenario_id,project_id,requirement_id,title,preconditions,actions,
                    expected_outcome,source_chunk_id,created_at,scenario_name,scenario_type,
                    related_requirements,actors,normal_flow,abnormal_flow,boundary_conditions,
                    source_chunk_ids,confidence,need_human_confirm,card_json,
                    compilation_status,review_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        scenario["scenario_id"], project_id,
                        (scenario.get("requirement_ids") or [""])[0], scenario["title"],
                        dumps_json(scenario.get("preconditions", [])),
                        dumps_json(scenario.get("normal_flow", [])),
                        dumps_json(scenario.get("success_criteria", [])),
                        (scenario.get("source_chunk_ids") or [""])[0], timestamp,
                        scenario["title"], scenario.get("scenario_category", ""),
                        dumps_json(scenario.get("requirement_ids", [])),
                        dumps_json(scenario.get("actors", [])),
                        dumps_json(scenario.get("normal_flow", [])),
                        dumps_json(scenario.get("abnormal_flows", [])),
                        dumps_json(scenario.get("boundary_conditions", [])),
                        dumps_json(scenario.get("source_chunk_ids", [])),
                        float(scenario.get("confidence") or 0),
                        int(bool(scenario.get("need_human_confirm"))), dumps_json(scenario),
                        "draft", "{}",
                    ),
                )
                for allocation in scenario.get("equipment_allocations", []):
                    conn.execute(
                        """INSERT INTO scenario_equipment_allocations(
                        allocation_id,project_id,scenario_id,role_requirement_id,
                        equipment_id,quantity,configuration_json,capability_ids_json,
                        rule_ids_json,assumptions_json,need_human_confirm,created_at,
                        updated_at,source_refs_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            allocation["allocation_id"], project_id, scenario["scenario_id"],
                            allocation["role_requirement_id"], allocation["equipment_id"],
                            allocation.get("quantity") or 0,
                            dumps_json(allocation.get("configuration", {})),
                            dumps_json(allocation.get("capability_ids", [])),
                            dumps_json(allocation.get("rule_ids", [])),
                            dumps_json(allocation.get("assumptions", [])),
                            int(bool(allocation.get("need_human_confirm"))), timestamp, timestamp,
                            dumps_json(allocation.get("source_refs", [])),
                        ),
                    )
            for validation in validations:
                conn.execute(
                    """INSERT INTO scenario_validation_results(
                    validation_id,project_id,scenario_id,is_valid,errors_json,warnings_json,
                    missing_roles_json,conflicting_rule_ids_json,checked_allocation_ids_json,
                    metrics_json,need_human_confirm,created_at,passed,score,
                    blocking_issues_json,missing_information_json,check_results_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        validation["validation_id"], project_id, validation["scenario_id"],
                        int(bool(validation["is_valid"])), dumps_json(validation.get("errors", [])),
                        dumps_json(validation.get("warnings", [])),
                        dumps_json(validation.get("missing_roles", [])),
                        dumps_json(validation.get("conflicting_rule_ids", [])),
                        dumps_json(validation.get("checked_allocation_ids", [])),
                        dumps_json(validation.get("metrics", {})),
                        int(bool(validation.get("need_human_confirm"))), timestamp,
                        int(bool(validation.get("passed"))), float(validation.get("score") or 0),
                        dumps_json(validation.get("blocking_issues", [])),
                        dumps_json(validation.get("missing_information", [])),
                        dumps_json(validation.get("check_results", {})),
                    ),
                )
            conn.execute(
                """INSERT INTO scenario_generation_runs(
                run_id,project_id,scenario_id,template_id,status,generation_mode,
                model_name,input_json,output_json,error_json,created_at,updated_at,
                provenance_json,step_trace_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, project_id, scenarios[0]["scenario_id"] if scenarios else "",
                    template_id or None, "completed", "deterministic_workflow", "",
                    dumps_json(input_data), dumps_json(scenarios), "{}", timestamp, timestamp,
                    dumps_json(provenance), dumps_json(step_trace),
                ),
            )
        return run_id

    def list_compiled(self, project_id: str, status: str = "") -> List[Dict[str, Any]]:
        query = "SELECT * FROM scenario_cards WHERE project_id=? AND compilation_status!='legacy'"
        params: tuple[Any, ...] = (project_id,)
        if status:
            query += " AND compilation_status=?"
            params += (status,)
        query += " ORDER BY created_at DESC,scenario_id"
        with self.connections.connection() as conn:
            result = []
            for row in conn.execute(query, params):
                item = dict(row)
                card = loads_json(item.get("card_json"), {})
                card["compilation_status"] = item.get("compilation_status")
                card["review"] = loads_json(item.get("review_json"), {})
                result.append(card)
            return result

    def review_compiled(
        self,
        project_id: str,
        scenario_id: str,
        *,
        accept_non_blocking: bool,
        blocking_resolutions: Dict[str, str],
        approve: bool,
        reviewer: str,
    ) -> Dict[str, Any]:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        with self.connections.transaction() as conn:
            scenario = conn.execute(
                """SELECT * FROM scenario_cards WHERE project_id=? AND scenario_id=?
                AND compilation_status!='legacy'""",
                (project_id, scenario_id),
            ).fetchone()
            if not scenario:
                raise ValueError("compiled scenario was not found in the bound project")
            validation = conn.execute(
                """SELECT * FROM scenario_validation_results WHERE project_id=? AND scenario_id=?
                ORDER BY created_at DESC,validation_id DESC LIMIT 1""",
                (project_id, scenario_id),
            ).fetchone()
            blockers = loads_json(validation["blocking_issues_json"], []) if validation else []
            unresolved = [item for item in blockers if not blocking_resolutions.get(item, "").strip()]
            if approve and unresolved:
                raise ValueError(f"blocking issues must be resolved individually: {unresolved}")
            review = {
                "reviewer": reviewer.strip(),
                "accepted_non_blocking_suggestions": bool(accept_non_blocking),
                "blocking_resolutions": {
                    key: value.strip() for key, value in blocking_resolutions.items() if value.strip()
                },
                "original_blocking_issues": blockers,
                "reviewed_at": now_iso(),
            }
            status = "approved" if approve else "draft"
            conn.execute(
                """UPDATE scenario_cards SET compilation_status=?,review_json=?
                WHERE project_id=? AND scenario_id=?""",
                (status, dumps_json(review), project_id, scenario_id),
            )
        return {"scenario_id": scenario_id, "compilation_status": status,
                "unresolved_blocking_issues": unresolved, "review": review}
