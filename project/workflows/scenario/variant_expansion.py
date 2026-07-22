"""Compatibility facade for deterministic scenario compilation."""

from typing import Any, Dict, List

from workflows.learning.knowledge_review_service import KnowledgeReviewService


def expand_scenario(seed: Dict[str, str]) -> List[Dict[str, str]]:
    """Compile a seed through the new workflow and return the legacy flat format."""
    from application.services.project_service import ProjectManager
    from workflows.scenario.scenario_workflow import ScenarioWorkflow

    project_id = str(seed.get("project_id") or "").strip()
    if not project_id:
        return []
    focus = seed.get("focus_risks", "")
    intent = {
        "scenario_goal": seed.get("scenario_goal") or seed.get("goal")
        or seed.get("scenario_name") or seed.get("title") or "",
        "simulation_object": seed.get("simulation_object")
        or seed.get("test_object") or "",
        "target_subsystem": seed.get("target_subsystem", ""),
        "mission_phase": seed.get("mission_phase", ""),
        "scale": seed.get("scale", 1),
        "focus_risks": [item.strip() for item in focus.split(",") if item.strip()],
        "use_project_defaults": str(seed.get("use_project_defaults", "true")).lower()
        not in {"false", "0", "no"},
        "additional_instructions": seed.get("additional_instructions", ""),
        "title": seed.get("title") or seed.get("scenario_name") or "",
    }
    manager = ProjectManager()
    if not manager.get_project(project_id):
        return []
    result = ScenarioWorkflow(manager).run(project_id, intent)
    return [
        {
            "scenario_id": item.scenario_id,
            "scenario_name": item.title,
            "scenario_type": item.scenario_category,
            "preconditions": "\n".join(item.preconditions),
            "actions": "\n".join(item.normal_flow),
            "expected_outcome": "\n".join(item.success_criteria),
            "source_chunk_ids": ",".join(item.source_chunk_ids),
            "need_human_confirm": str(item.need_human_confirm).lower(),
        }
        for item in result.scenarios
    ]


def get_scenario_knowledge(
    manager: Any,
    project_id: str,
    *,
    section_scope: str = "",
    include_global: bool = False,
) -> List[Dict[str, Any]]:
    """Return only generation-safe knowledge in the requested project scope."""
    return KnowledgeReviewService(manager).query_for_scenario(
        project_id, section_scope=section_scope, include_global=include_global
    )
