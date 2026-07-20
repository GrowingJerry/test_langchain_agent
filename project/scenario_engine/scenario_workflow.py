"""Deterministic, provenance-preserving scenario compilation workflow."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from domain.schemas.allocation import EquipmentAllocation, ScenarioValidationResult
from domain.schemas.scenario_spec import ScenarioIntent, ScenarioRoleRequirement, ScenarioSpec
from equipment.allocation_rules import AllocationRule
from equipment.equipment_service import EquipmentService
from infrastructure.db.json_codec import loads_json
from infrastructure.db.repositories.base import new_id
from scenario_engine.context_builder import ScenarioContextBuilder
from scenario_engine.equipment_allocator import EquipmentAllocator
from scenario_engine.intent_parser import IntentParser
from scenario_engine.role_planner import RolePlanner
from scenario_engine.scenario_validator import ScenarioValidator
from scenario_engine.template_matcher import TemplateMatcher
from scenario_engine.variant_generator import VariantGenerator

LanguageChain = Callable[[ScenarioSpec, Dict[str, Any]], ScenarioSpec]
ProgressCallback = Callable[[str, float], None]


class ScenarioWorkflowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    intent: ScenarioIntent
    scenarios: List[ScenarioSpec] = Field(default_factory=list)
    validations: List[ScenarioValidationResult] = Field(default_factory=list)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    step_trace: List[Dict[str, Any]] = Field(default_factory=list)


class ScenarioWorkflow:
    def __init__(self, manager: Any, language_chain: Optional[LanguageChain] = None) -> None:
        self.manager = manager
        self.intent_parser = IntentParser()
        self.context_builder = ScenarioContextBuilder(manager)
        self.template_matcher = TemplateMatcher(manager)
        self.role_planner = RolePlanner()
        self.variant_generator = VariantGenerator()
        self.validator = ScenarioValidator(manager)
        self.equipment_service = EquipmentService(manager.equipment)
        self.allocator = EquipmentAllocator(manager.equipment)
        self.language_chain = language_chain

    def run(
        self,
        project_id: str,
        value: ScenarioIntent | Dict[str, Any],
        progress_callback: Optional[ProgressCallback] = None,
    ) -> ScenarioWorkflowResult:
        def progress(stage: str, value: float) -> None:
            if progress_callback:
                progress_callback(stage, value)

        trace: List[Dict[str, Any]] = []
        progress("解析场景意图", 0.08)
        intent = self.intent_parser.parse(project_id, value)
        trace.append({"step": "parse_intent", "output": intent.model_dump(mode="json")})
        progress("读取项目资料与approved知识", 0.2)
        context = self.context_builder.build(intent)
        trace.append({"step": "build_context", "requirement_ids": [row.get("requirement_id") for row in context["requirements"]],
                      "knowledge_unit_ids": context["knowledge_unit_ids"], "source_chunk_ids": context["source_chunk_ids"],
                      "feedback_rule_ids": context["feedback_rule_ids"]})
        progress("匹配approved场景模板", 0.35)
        template = self.template_matcher.match(intent)
        trace.append({"step": "match_template", "template_id": (template or {}).get("template_id", "")})
        progress("规划角色并匹配装备", 0.5)
        roles = self.role_planner.plan(intent, context, template)
        trace.append({"step": "plan_roles", "roles": [role.model_dump(mode="json") for role in roles]})
        progress("执行装备数量规则", 0.62)
        allocations, equipment_trace, missing = self._allocate(
            intent, roles, context["feedback_rules"]
        )
        trace.append({"step": "allocate_equipment", "matches": equipment_trace, "missing": missing})
        progress("生成主场景与必要变体", 0.75)
        base = self._build_base(intent, context, template, roles, allocations, missing)
        base = self._apply_feedback_rules(base, context["feedback_rules"])
        trace.append({"step": "apply_approved_feedback",
                      "feedback_rule_ids": context["feedback_rule_ids"]})
        if self.language_chain:
            candidate = self.language_chain(base, context)
            base = self._accept_language_only(base, candidate)
            trace.append({"step": "structured_language_chain", "accepted": True})
        variants = self.variant_generator.select_types(intent, context, template)
        scenarios = self.variant_generator.generate(base, variants)
        scenarios = [self._bind_allocations(item) for item in scenarios]
        trace.append({"step": "generate_variants", "variant_types": [item.scenario_category for item in scenarios]})
        provenance = {
            "project_id": project_id,
            "allow_global": intent.use_project_defaults,
            "learning_goal": intent.effective_goal,
            "requirement_ids": [str(row.get("requirement_id")) for row in context["requirements"]],
            "knowledge_unit_ids": context["knowledge_unit_ids"],
            "source_chunk_ids": context["source_chunk_ids"],
            "template_id": (template or {}).get("template_id", ""),
            "equipment": equipment_trace,
            "feedback_rule_ids": context["feedback_rule_ids"],
            "feedback_rule_matches": context["feedback_rules"],
            "field_sources": self._field_sources(context, template),
        }
        progress("执行真实性与完整性校验", 0.88)
        validations = [self.validator.validate(item, provenance) for item in scenarios]
        trace.append({"step": "validate", "valid": [item.passed for item in validations]})
        progress("保存场景草稿与来源", 0.96)
        run_id = self.manager.scenarios.save_compilation(
            project_id,
            [item.model_dump(mode="json") for item in scenarios],
            [item.model_dump(mode="json") for item in validations],
            input_data=intent.model_dump(mode="json"),
            template_id=str((template or {}).get("template_id") or ""),
            provenance=provenance,
            step_trace=trace,
        )
        progress("场景编译完成", 1.0)
        return ScenarioWorkflowResult(run_id=run_id, intent=intent, scenarios=scenarios,
                                      validations=validations, provenance=provenance, step_trace=trace)

    @staticmethod
    def _field_sources(context: Dict[str, Any], template: Dict[str, Any] | None) -> Dict[str, Any]:
        sources: Dict[str, Any] = {
            "normal_flow": {"template_id": (template or {}).get("template_id", ""),
                            "chunk_ids": context["source_chunk_ids"]},
            "preconditions": {"knowledge_unit_ids": context["knowledge_unit_ids"]},
        }
        for row in context["knowledge"]:
            if row.get("knowledge_type") not in {"state_variable", "input_output"}:
                continue
            data = row.get("normalized_data", {})
            name = str(data.get("name") or data.get("symbol") or row.get("title") or "")
            if name:
                sources[f"observed_variables.{name}"] = {
                    "knowledge_unit_id": row["knowledge_unit_id"],
                    "chunk_id": row.get("chunk_id"),
                }
        return sources

    def _allocate(
        self, intent: ScenarioIntent, roles: List[ScenarioRoleRequirement],
        feedback_rules: List[Dict[str, Any]],
    ) -> Tuple[List[EquipmentAllocation], List[Dict[str, Any]], List[str]]:
        selected: List[Tuple[ScenarioRoleRequirement, Any]] = []
        trace: List[Dict[str, Any]] = []
        missing: List[str] = []
        for role in roles:
            result = self.equipment_service.search(
                intent.project_id, roles=[role.role_name], capabilities=role.required_capabilities,
                allow_global=intent.use_project_defaults,
            )
            if not result.matches:
                missing.extend([f"角色 {role.role_name}: {item}" for item in result.missing_information])
                continue
            preferred_ids = [
                str((rule.get("action") or {}).get("value") or "")
                for rule in feedback_rules
                if rule.get("rule_type") == "equipment_preference_rule"
                and (rule.get("action") or {}).get("field_name") in {
                    "equipment_id", "equipment", "装备"
                }
            ]
            match = next(
                (item for equipment_id in preferred_ids for item in result.matches
                 if item.equipment_id == equipment_id),
                result.matches[0],
            )
            selected.append((role, match))
            trace.append(match.model_dump(mode="json"))
        rules = self._load_rules(intent.project_id, selected, intent.use_project_defaults)
        requested = [(role.role_name, match.equipment_id) for role, match in selected]
        solutions = self.allocator.allocate(
            intent.project_id, rules,
            input_values={"scale": intent.scale} if isinstance(intent.scale, (int, float)) else {},
            requested_allocations=requested, allow_global=intent.use_project_defaults,
        ) if requested else []
        by_key = {(item.role, item.equipment_id): item for item in solutions}
        allocations = []
        for role, match in selected:
            solution = by_key[(role.role_name, match.equipment_id)]
            allocations.append(EquipmentAllocation(
                allocation_id=new_id("ALLOC"), project_id=intent.project_id,
                scenario_id="", role_requirement_id=role.role_requirement_id,
                equipment_id=match.equipment_id, quantity=solution.quantity,
                configuration={
                    "quantity_source": solution.quantity_source,
                    "formula_description": solution.formula_description,
                    "input_values": solution.input_values,
                    "constraints_checked": solution.constraints_checked,
                },
                capability_ids=match.matched_capabilities,
                rule_ids=[solution.rule_id] if solution.rule_id else [],
                assumptions=list(solution.warnings),
                source_refs=[*match.source_refs, *solution.source_refs],
                need_human_confirm=bool(solution.need_human_confirm or solution.quantity is None),
            ))
        return allocations, trace, missing

    def _load_rules(self, project_id: str, selected: List[Tuple[ScenarioRoleRequirement, Any]], allow_global: bool) -> List[AllocationRule]:
        scopes = [project_id, *(["GLOBAL"] if allow_global else [])]
        selected_keys = {(role.role_name, match.equipment_id) for role, match in selected}
        placeholders = ",".join("?" for _ in scopes)
        with self.manager.connections.connection() as conn:
            rows = [dict(row) for row in conn.execute(
                f"SELECT * FROM equipment_configuration_rules WHERE project_id IN ({placeholders}) AND enabled=1",
                tuple(scopes),
            )]
        rules = []
        for row in rows:
            payload = loads_json(row.get("parameters_json"), {})
            role_names = loads_json(row.get("required_roles_json"), [])
            for role, equipment_id in sorted(selected_keys):
                target_equipment = str(payload.get("equipment_id") or "")
                if role not in role_names or (target_equipment and target_equipment != equipment_id):
                    continue
                rule_type = payload.get("rule_type") or row.get("condition_expression")
                parameters = payload.get("parameters") or {
                    key: value for key, value in payload.items() if key not in {"rule_type", "equipment_id"}
                }
                try:
                    rules.append(AllocationRule(
                        rule_id=row["rule_id"], role=role, equipment_id=equipment_id,
                        rule_type=rule_type, parameters=parameters,
                        source_refs=[{key: row.get(key) for key in ("document_id", "chunk_id", "page_no", "jsonl_record_no") if row.get(key) is not None}],
                    ))
                except (ValueError, TypeError):
                    continue
        return rules

    def _build_base(self, intent: ScenarioIntent, context: Dict[str, Any], template: Dict[str, Any] | None,
                    roles: List[ScenarioRoleRequirement], allocations: List[EquipmentAllocation], missing: List[str]) -> ScenarioSpec:
        payload = (template or {}).get("template", {})
        knowledge = context["knowledge"]
        observed: Dict[str, Any] = {}
        for row in knowledge:
            if row.get("knowledge_type") in {"state_variable", "input_output"}:
                data = row.get("normalized_data", {})
                name = str(data.get("name") or data.get("symbol") or row.get("title") or "")
                if name:
                    observed[name] = data
        human_observation = not observed
        if human_observation:
            observed["goal_result"] = {"description": "用户目标的可观测结果，定义待确认"}
        boundary = [str(row.get("content")) for row in knowledge if row.get("knowledge_type") == "constraint"]
        recovery = []
        for row in knowledge:
            value = row.get("normalized_data", {}).get("recovery_flow")
            if isinstance(value, list):
                recovery.extend(str(item) for item in value)
        preconditions = list(payload.get("preconditions") or [])
        preconditions.extend(item for row in knowledge for item in row.get("applicable_conditions", []))
        normal_flow = list(payload.get("normal_flow") or [f"执行场景目标：{intent.effective_goal}"])
        criteria = [f"观察 {name}：满足场景目标“{intent.effective_goal}”" for name in observed]
        source_ids = context["source_chunk_ids"]
        requirement_ids = [str(row.get("requirement_id")) for row in context["requirements"]]
        return ScenarioSpec(
            scenario_id=new_id("SCN"), project_id=intent.project_id,
            title=intent.title or intent.effective_goal, scenario_goal=intent.effective_goal,
            scenario_category="nominal", mission_phase=intent.mission_phase,
            simulation_object=intent.simulation_object,
            initial_state=dict(payload.get("initial_state") or {}),
            actors=[role.role_name for role in roles], role_requirements=roles,
            preconditions=list(dict.fromkeys(preconditions)),
            trigger_events=list(payload.get("trigger_events") or []), normal_flow=normal_flow,
            boundary_conditions=list(dict.fromkeys([*payload.get("boundary_conditions", []), *boundary])),
            recovery_flow=list(dict.fromkeys([*payload.get("recovery_flow", []), *recovery])),
            environment_variables=dict(payload.get("environment_variables") or {}),
            controllable_variables=dict(payload.get("controllable_variables") or {}),
            disturbance_variables=dict(payload.get("disturbance_variables") or {}),
            observed_variables=observed, equipment_allocations=allocations,
            success_criteria=criteria, failure_criteria=list(payload.get("failure_criteria") or []),
            requirement_ids=requirement_ids, source_chunk_ids=source_ids,
            knowledge_unit_ids=context["knowledge_unit_ids"], assumptions=intent.assumptions,
            missing_information=list(dict.fromkeys([*missing, *(["可观测变量定义待人工确认"] if human_observation else [])])),
            need_human_confirm=bool(missing or human_observation or any(item.need_human_confirm for item in allocations)),
            confidence=0.85 if source_ids or knowledge else 0.4,
        )

    @staticmethod
    def _apply_feedback_rules(
        scenario: ScenarioSpec, rules: List[Dict[str, Any]]
    ) -> ScenarioSpec:
        """Apply only explicitly approved, schema-bounded feedback actions."""
        updates: Dict[str, Any] = {}
        allowed = {
            "scenario_completion_rule": {"preconditions", "trigger_events", "normal_flow",
                                         "boundary_conditions", "recovery_flow"},
            "parameter_usage_rule": {"environment_variables", "controllable_variables",
                                     "disturbance_variables", "observed_variables"},
            "validation_rule": {"success_criteria", "failure_criteria"},
            "writing_style_rule": {"title"},
        }
        for rule in rules:
            action = rule.get("action") or {}
            field_name = str(action.get("field_name") or "")
            if field_name not in allowed.get(str(rule.get("rule_type")), set()):
                continue
            value = action.get("value")
            current = updates.get(field_name, getattr(scenario, field_name))
            if isinstance(current, list):
                additions = value if isinstance(value, list) else [value]
                updates[field_name] = list(dict.fromkeys([*current, *[item for item in additions if item]]))
            elif isinstance(current, dict) and isinstance(value, dict):
                updates[field_name] = {**current, **value}
            elif field_name == "title" and isinstance(value, str) and value.strip():
                updates[field_name] = value.strip()
        return scenario.model_copy(update=updates) if updates else scenario

    @staticmethod
    def _accept_language_only(base: ScenarioSpec, candidate: ScenarioSpec) -> ScenarioSpec:
        immutable_fields = set(ScenarioSpec.model_fields) - {"title"}
        if any(getattr(candidate, field) != getattr(base, field) for field in immutable_fields):
            raise ValueError("structured language chain attempted to add ungrounded facts")
        return candidate

    @staticmethod
    def _bind_allocations(scenario: ScenarioSpec) -> ScenarioSpec:
        allocations = [item.model_copy(update={"allocation_id": new_id("ALLOC"), "scenario_id": scenario.scenario_id})
                       for item in scenario.equipment_allocations]
        return scenario.model_copy(update={"equipment_allocations": allocations})
