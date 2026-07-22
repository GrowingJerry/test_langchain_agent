"""Evidence-driven scenario variant selection and generation."""

from __future__ import annotations

from typing import Any, Dict, List

from domain.schemas.scenario_spec import ScenarioIntent, ScenarioSpec
from infrastructure.repositories.base import new_id


VARIANT_TYPES = {
    "nominal", "boundary", "abnormal", "degraded", "recovery", "concurrency",
    "communication_or_data_fault",
}


class VariantGenerator:
    def select_types(self, intent: ScenarioIntent, context: Dict[str, Any], template: Dict[str, Any] | None) -> List[str]:
        text = " ".join([*intent.focus_risks, intent.additional_instructions]).lower()
        knowledge_types = {row.get("knowledge_type") for row in context["knowledge"]}
        selected = ["nominal"]
        mapping = [
            ("boundary", {"constraint"}, ("边界", "极限", "boundary")),
            ("abnormal", {"fault_mode"}, ("异常", "故障", "fault", "abnormal")),
            ("degraded", set(), ("降级", "degraded")),
            ("concurrency", set(), ("并发", "concurrency")),
            ("communication_or_data_fault", {"input_output"}, ("通信", "数据", "接口", "communication", "data fault")),
        ]
        for variant, types, terms in mapping:
            if (types and knowledge_types & types) or any(term in text for term in terms):
                selected.append(variant)
        requested = (template or {}).get("template", {}).get("variant_types") or []
        selected.extend(item for item in requested if item in VARIANT_TYPES)
        if "abnormal" in selected and (
            "state_transition" in knowledge_types or "恢复" in text or "recovery" in text
        ):
            selected.append("recovery")
        return list(dict.fromkeys(selected))[:5]

    def generate(self, base: ScenarioSpec, variant_types: List[str]) -> List[ScenarioSpec]:
        result = [base.model_copy(update={"scenario_category": "nominal"})]
        for variant in variant_types:
            if variant == "nominal":
                continue
            updates: Dict[str, Any] = {
                "scenario_id": new_id("SCN"), "title": f"{base.title} - {variant}",
                "scenario_category": variant,
            }
            if variant in {"abnormal", "degraded", "communication_or_data_fault"}:
                response = base.recovery_flow or ["系统记录异常并进入受控状态，具体恢复动作待人工确认"]
                updates["abnormal_flows"] = [[f"注入{variant}条件", *response]]
                updates["need_human_confirm"] = bool(base.need_human_confirm or not base.recovery_flow)
            elif variant == "boundary":
                updates["boundary_conditions"] = base.boundary_conditions or ["边界取值待人工确认"]
                updates["need_human_confirm"] = bool(base.need_human_confirm or not base.boundary_conditions)
            elif variant == "recovery":
                updates["trigger_events"] = ["触发已识别异常后的恢复过程"]
            elif variant == "concurrency":
                updates["trigger_events"] = ["同时触发已识别的角色活动"]
            result.append(base.model_copy(update=updates))
        return result
