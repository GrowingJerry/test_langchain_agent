"""Deterministic functional role requirements from approved facts and templates."""

from __future__ import annotations

from typing import Any, Dict, List

from domain.schemas.scenario_spec import ScenarioIntent, ScenarioRoleRequirement
from infrastructure.repositories.base import new_id


class RolePlanner:
    def plan(
        self, intent: ScenarioIntent, context: Dict[str, Any], template: Dict[str, Any] | None
    ) -> List[ScenarioRoleRequirement]:
        raw_roles = list((template or {}).get("template", {}).get("role_requirements") or [])
        for rule in context.get("feedback_rules", []):
            if rule.get("rule_type") != "role_mapping_rule":
                continue
            value = (rule.get("action") or {}).get("value")
            raw_roles.extend(value if isinstance(value, list) else [value] if isinstance(value, dict) else [])
        if not raw_roles:
            for unit in context["knowledge"]:
                if unit.get("knowledge_type") == "scenario_pattern":
                    raw_roles.extend(unit.get("normalized_data", {}).get("role_requirements") or [])
        if not raw_roles:
            name = intent.target_subsystem or intent.simulation_object
            raw_roles = [{
                "role_name": name,
                "description": f"执行“{intent.effective_goal}”中与{name}相关的功能",
                "required_capabilities": [],
                "min_quantity": 1,
                "max_quantity": 1,
            }]
        roles = []
        seen = set()
        for raw in raw_roles:
            name = str(raw.get("role_name") or raw.get("role") or "").strip()
            description = str(raw.get("description") or raw.get("function") or "").strip()
            if not name or not description or name in seen:
                continue
            seen.add(name)
            roles.append(ScenarioRoleRequirement(
                role_requirement_id=str(raw.get("role_requirement_id") or new_id("ROLE")),
                role_name=name, description=description,
                min_quantity=max(0, int(raw.get("min_quantity", 1))),
                max_quantity=max(0, int(raw.get("max_quantity", raw.get("min_quantity", 1)))),
                required_capabilities=list(raw.get("required_capabilities") or []),
                constraints=list(raw.get("constraints") or []),
                optional=bool(raw.get("optional", False)),
            ))
        if not roles:
            raise ValueError("没有可追踪且说明功能的场景角色")
        return roles
