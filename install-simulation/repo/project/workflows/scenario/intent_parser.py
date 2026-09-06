"""Validation and normalization for minimal scenario compilation input."""

from __future__ import annotations

from typing import Any, Dict

from domain.schemas.scenario_spec import ScenarioIntent
from infrastructure.repositories.base import new_id


class IntentParser:
    def parse(self, project_id: str, value: ScenarioIntent | Dict[str, Any]) -> ScenarioIntent:
        if isinstance(value, ScenarioIntent):
            intent = value
            if intent.project_id != project_id:
                raise ValueError("ScenarioIntent project_id does not match workflow scope")
        else:
            data = dict(value)
            data["project_id"] = project_id
            data.setdefault("intent_id", new_id("INT"))
            data.setdefault("title", str(data.get("scenario_goal") or data.get("goal") or "场景"))
            intent = ScenarioIntent.model_validate(data)
        if not intent.effective_goal.strip() or not intent.simulation_object.strip():
            raise ValueError("scenario_goal and simulation_object are required")
        if isinstance(intent.scale, (int, float)) and intent.scale < 0:
            raise ValueError("scale must be non-negative")
        return intent
