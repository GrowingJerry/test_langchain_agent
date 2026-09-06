"""Deterministic, project-scoped validation for compiled scenarios."""

from __future__ import annotations

from typing import Any, Dict, List

from domain.schemas.allocation import ScenarioValidationResult
from domain.schemas.scenario_spec import ScenarioSpec
from infrastructure.database.json_codec import loads_json
from infrastructure.repositories.base import new_id

CHECK_NAMES = (
    "requirement_grounding", "source_grounding", "state_consistency",
    "role_completeness", "equipment_role_consistency", "quantity_traceability",
    "parameter_traceability", "observability", "contradiction",
    "recovery_completeness", "project_scope", "book_override",
)


class ScenarioValidator:
    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def validate(
        self, scenario: ScenarioSpec, provenance: Dict[str, Any] | None = None
    ) -> ScenarioValidationResult:
        provenance = dict(provenance or {})
        results = {name: {"passed": True, "issues": []} for name in CHECK_NAMES}
        blocking: List[str] = []
        warnings: List[str] = []
        missing: List[str] = []

        def issue(check: str, message: str, *, block: bool = False, absent: bool = False) -> None:
            results[check]["passed"] = False
            results[check]["issues"].append(message)
            (blocking if block else warnings).append(f"{check}: {message}")
            if absent:
                missing.append(message)

        self._requirement_grounding(scenario, provenance, issue)
        self._scope_and_sources(scenario, provenance, issue)
        self._state_consistency(scenario, issue)
        self._roles_and_equipment(scenario, provenance, issue)
        self._quantity_traceability(scenario, issue)
        self._parameter_traceability(scenario, provenance, issue)
        self._observability(scenario, issue)
        self._contradictions(scenario, issue)
        self._recovery(scenario, issue)
        self._book_override(scenario, issue)

        failed_checks = sum(not value["passed"] for value in results.values())
        score = round(max(0.0, 100.0 * (len(results) - failed_checks) / len(results)), 2)
        passed = not blocking
        all_issues = list(dict.fromkeys([*blocking, *warnings]))
        return ScenarioValidationResult(
            validation_id=new_id("VAL"), project_id=scenario.project_id,
            scenario_id=scenario.scenario_id, is_valid=passed, passed=passed,
            score=score, errors=list(dict.fromkeys(blocking)),
            blocking_issues=list(dict.fromkeys(blocking)),
            warnings=list(dict.fromkeys(warnings)),
            missing_information=list(dict.fromkeys(missing)), check_results=results,
            missing_roles=[role.role_name for role in scenario.role_requirements
                           if not role.optional and role.role_requirement_id not in
                           {item.role_requirement_id for item in scenario.equipment_allocations}],
            checked_allocation_ids=[item.allocation_id for item in scenario.equipment_allocations],
            metrics={"passed_checks": len(results) - failed_checks,
                     "total_checks": len(results), "issue_count": len(all_issues)},
            need_human_confirm=bool(scenario.need_human_confirm or all_issues),
        )

    def _requirement_grounding(self, scenario: ScenarioSpec, provenance: Dict[str, Any], issue: Any) -> None:
        valid = {str(row.get("requirement_id")) for row in self.manager.list_requirements(scenario.project_id)}
        invalid = [item for item in scenario.requirement_ids if item not in valid]
        if invalid:
            issue("requirement_grounding", f"引用不存在的需求：{invalid}", block=True)
        if not scenario.requirement_ids and not (
            scenario.scenario_goal.strip() or str(provenance.get("learning_goal") or "").strip()
        ):
            issue("requirement_grounding", "缺少需求关联或明确学习目标", absent=True)

    def _scope_and_sources(self, scenario: ScenarioSpec, provenance: Dict[str, Any], issue: Any) -> None:
        allow_global = bool(provenance.get("allow_global"))
        with self.manager.connections.connection() as conn:
            chunks = {row["chunk_id"]: row["project_id"] for row in conn.execute(
                "SELECT chunk_id,project_id FROM project_chunks WHERE chunk_id IN (%s)" %
                (",".join("?" for _ in scenario.source_chunk_ids) or "NULL"),
                tuple(scenario.source_chunk_ids),
            )}
            knowledge = {row["knowledge_unit_id"]: dict(row) for row in conn.execute(
                "SELECT * FROM knowledge_units WHERE knowledge_unit_id IN (%s)" %
                (",".join("?" for _ in scenario.knowledge_unit_ids) or "NULL"),
                tuple(scenario.knowledge_unit_ids),
            )}
        nonexistent = [item for item in scenario.source_chunk_ids if item not in chunks]
        if nonexistent:
            issue("source_grounding", f"引用不存在的chunk：{nonexistent}", block=True)
        bad_chunks = [item for item, scope in chunks.items() if scope != scenario.project_id]
        bad_knowledge = [item for item, row in knowledge.items()
                         if row["project_id"] != scenario.project_id
                         and not (allow_global and row["project_id"] == "GLOBAL")]
        if bad_chunks or bad_knowledge:
            issue("project_scope", f"引用其他项目数据：chunks={bad_chunks}, knowledge={bad_knowledge}", block=True)
        missing_knowledge = [item for item in scenario.knowledge_unit_ids if item not in knowledge]
        if missing_knowledge:
            issue("source_grounding", f"引用不存在的知识：{missing_knowledge}", block=True)
        factual = bool(scenario.initial_state or scenario.preconditions or scenario.normal_flow
                       or scenario.environment_variables or scenario.observed_variables)
        if factual and not scenario.source_chunk_ids and not scenario.knowledge_unit_ids:
            issue("source_grounding", "事实字段缺少项目资料或知识来源", absent=True)
        if factual and not provenance.get("field_sources"):
            issue("source_grounding", "未提供逐字段来源映射", absent=True)
        for field_name, refs in (provenance.get("field_sources") or {}).items():
            if not isinstance(refs, dict):
                issue("source_grounding", f"字段{field_name}的来源格式无效", block=True)
                continue
            referenced_chunks = [
                *([refs["chunk_id"]] if refs.get("chunk_id") else []),
                *(refs.get("chunk_ids") or []),
            ]
            referenced_knowledge = [
                *([refs["knowledge_unit_id"]] if refs.get("knowledge_unit_id") else []),
                *(refs.get("knowledge_unit_ids") or []),
            ]
            if any(item not in chunks for item in referenced_chunks):
                issue("source_grounding", f"字段{field_name}引用不存在的chunk", block=True)
            if any(item not in knowledge for item in referenced_knowledge):
                issue("source_grounding", f"字段{field_name}引用不存在的知识", block=True)

    @staticmethod
    def _state_consistency(scenario: ScenarioSpec, issue: Any) -> None:
        if scenario.initial_state and not scenario.normal_flow:
            issue("state_consistency", "存在初始状态但没有后续流程", absent=True)
        if scenario.trigger_events and not (scenario.normal_flow or scenario.abnormal_flows):
            issue("state_consistency", "触发事件没有对应的后续状态或流程", absent=True)
        for name, value in scenario.initial_state.items():
            if isinstance(value, dict) and value.get("before") == value.get("after") and value.get("transition_required"):
                issue("state_consistency", f"状态{name}要求转换但前后值相同")

    def _roles_and_equipment(self, scenario: ScenarioSpec, provenance: Dict[str, Any], issue: Any) -> None:
        role_by_id = {role.role_requirement_id: role for role in scenario.role_requirements}
        allocated = {item.role_requirement_id for item in scenario.equipment_allocations}
        missing_roles = [role.role_name for role in scenario.role_requirements
                         if not role.optional and role.role_requirement_id not in allocated]
        if missing_roles:
            issue("role_completeness", f"必要角色未配置装备：{missing_roles}", absent=True)
        allow_global = bool(provenance.get("allow_global"))
        for allocation in scenario.equipment_allocations:
            role = role_by_id.get(allocation.role_requirement_id)
            if role is None:
                issue("equipment_role_consistency", f"装备{allocation.equipment_id}对应的角色不存在", block=True)
                continue
            equipment = self.manager.equipment.get_equipment(
                scenario.project_id, allocation.equipment_id, allow_global=allow_global
            )
            if not equipment:
                with self.manager.connections.connection() as conn:
                    foreign = conn.execute(
                        "SELECT project_id FROM equipment_entities WHERE equipment_id=?",
                        (allocation.equipment_id,),
                    ).fetchone()
                if foreign and foreign["project_id"] != scenario.project_id:
                    issue("project_scope", f"装备引用其他项目：{allocation.equipment_id}", block=True)
                issue("equipment_role_consistency", f"装备不存在或超出项目作用域：{allocation.equipment_id}", block=True)
                continue
            capabilities = {str(item.get("name") or "") for item in equipment.get("capabilities", [])}
            missing_caps = [item for item in role.required_capabilities if item not in capabilities]
            if missing_caps:
                issue("equipment_role_consistency", f"装备{allocation.equipment_id}缺少能力：{missing_caps}")

    @staticmethod
    def _quantity_traceability(scenario: ScenarioSpec, issue: Any) -> None:
        for allocation in scenario.equipment_allocations:
            if allocation.quantity is None:
                if not allocation.need_human_confirm:
                    issue("quantity_traceability", f"装备{allocation.equipment_id}数量为空但未要求人工确认")
                continue
            user_source = any("用户" in item for item in allocation.assumptions) or allocation.configuration.get("quantity_source") == "user"
            if not allocation.rule_ids and not user_source:
                issue("quantity_traceability", f"装备{allocation.equipment_id}数量{allocation.quantity}没有rule_id或用户来源", block=True)

    @staticmethod
    def _parameter_traceability(scenario: ScenarioSpec, provenance: Dict[str, Any], issue: Any) -> None:
        field_sources = provenance.get("field_sources") or {}
        groups = {
            "initial_state": scenario.initial_state,
            "environment_variables": scenario.environment_variables,
            "controllable_variables": scenario.controllable_variables,
            "disturbance_variables": scenario.disturbance_variables,
            "observed_variables": scenario.observed_variables,
        }
        for group_name, values in groups.items():
            for name, raw in values.items():
                if isinstance(raw, bool):
                    continue
                if isinstance(raw, (int, float)):
                    issue("parameter_traceability", f"数值参数{group_name}.{name}缺少单位、条件和来源", absent=True)
                elif isinstance(raw, dict) and isinstance(raw.get("value"), (int, float)):
                    absent = [key for key in ("unit", "operating_condition") if not raw.get(key)]
                    has_source = bool(raw.get("source_ref") or raw.get("source_chunk_id")
                                      or raw.get("knowledge_unit_id") or field_sources.get(f"{group_name}.{name}"))
                    if absent or not has_source:
                        issue("parameter_traceability", f"数值参数{group_name}.{name}缺少{absent or ['来源']}", absent=True)

    @staticmethod
    def _observability(scenario: ScenarioSpec, issue: Any) -> None:
        if not scenario.normal_flow:
            issue("observability", "没有可与预期结果对应的执行步骤", block=True)
        if not scenario.success_criteria:
            issue("observability", "缺少成功标准", block=True, absent=True)
            return
        names = tuple(scenario.observed_variables)
        for criterion in scenario.success_criteria:
            if not names or not any(name in criterion for name in names):
                issue("observability", f"步骤与预期结果无法对应可观测变量：{criterion}", block=True)

    @staticmethod
    def _contradictions(scenario: ScenarioSpec, issue: Any) -> None:
        statements = [*scenario.preconditions, *[str(item) for item in scenario.environment_variables.values()],
                      *scenario.normal_flow]
        normalized = {item.strip().lower() for item in statements if item.strip()}
        for value in list(normalized):
            opposites = {f"非{value}", f"不{value}", f"not {value}"}
            if normalized & opposites:
                issue("contradiction", f"条件或流程存在矛盾：{value}")
                break
        for name, value in scenario.initial_state.items():
            if name in scenario.environment_variables and scenario.environment_variables[name] != value:
                issue("contradiction", f"初始状态与环境变量冲突：{name}")

    @staticmethod
    def _recovery(scenario: ScenarioSpec, issue: Any) -> None:
        if not scenario.abnormal_flows:
            return
        if not scenario.recovery_flow:
            issue("recovery_completeness", "异常场景缺少恢复流程或恢复条件", absent=True)
            return
        recovery_text = " ".join(scenario.recovery_flow).lower()
        if not any(term in recovery_text for term in ("当", "条件", "后", "if", "when", "恢复")):
            issue("recovery_completeness", "恢复流程没有说明恢复条件", absent=True)

    def _book_override(self, scenario: ScenarioSpec, issue: Any) -> None:
        if not scenario.knowledge_unit_ids:
            return
        with self.manager.connections.connection() as conn:
            selected = [dict(row) for row in conn.execute(
                "SELECT * FROM knowledge_units WHERE knowledge_unit_id IN (%s)" %
                ",".join("?" for _ in scenario.knowledge_unit_ids), tuple(scenario.knowledge_unit_ids))]
            approved = [dict(row) for row in conn.execute(
                "SELECT * FROM knowledge_units WHERE project_id=? AND status='approved'",
                (scenario.project_id,))]
        for book in (row for row in selected if row.get("source_kind") == "book"):
            book_data = loads_json(book.get("normalized_data_json"), {})
            name = str(book_data.get("name") or book.get("title") or "")
            for project in approved:
                project_data = loads_json(project.get("normalized_data_json"), {})
                project_name = str(project_data.get("name") or project.get("title") or "")
                if name and name == project_name and book_data.get("value") != project_data.get("value"):
                    issue("book_override", f"通用书籍参数{name}覆盖当前项目approved事实", block=True)
                    return
