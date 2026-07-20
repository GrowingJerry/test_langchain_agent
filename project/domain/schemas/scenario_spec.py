"""Canonical compiled-scenario specification schemas."""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field

from domain.schemas.allocation import EquipmentAllocation


class ScenarioIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str = ""
    project_id: str
    title: str = ""
    goal: str = ""
    scenario_goal: str = ""
    simulation_object: str = ""
    target_subsystem: str = ""
    category: str = ""
    mission_phase: str = ""
    scale: Any = 1
    focus_risks: List[str] = Field(default_factory=list)
    use_project_defaults: bool = True
    additional_instructions: str = ""
    requirement_ids: List[str] = Field(default_factory=list)
    source_chunk_ids: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)

    @property
    def effective_goal(self) -> str:
        return self.scenario_goal or self.goal


class ScenarioRoleRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_requirement_id: str
    role_name: str
    description: str = ""
    min_quantity: int = Field(default=1, ge=0)
    max_quantity: int = Field(default=1, ge=0)
    required_capabilities: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    optional: bool = False


class ScenarioSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    project_id: str
    title: str
    scenario_goal: str
    scenario_category: str = ""
    mission_phase: str = ""
    simulation_object: str = ""
    initial_state: Dict[str, Any] = Field(default_factory=dict)
    actors: List[str] = Field(default_factory=list)
    role_requirements: List[ScenarioRoleRequirement] = Field(default_factory=list)
    preconditions: List[str] = Field(default_factory=list)
    trigger_events: List[str] = Field(default_factory=list)
    normal_flow: List[str] = Field(default_factory=list)
    abnormal_flows: List[List[str]] = Field(default_factory=list)
    boundary_conditions: List[str] = Field(default_factory=list)
    recovery_flow: List[str] = Field(default_factory=list)
    environment_variables: Dict[str, Any] = Field(default_factory=dict)
    controllable_variables: Dict[str, Any] = Field(default_factory=dict)
    disturbance_variables: Dict[str, Any] = Field(default_factory=dict)
    observed_variables: Dict[str, Any] = Field(default_factory=dict)
    equipment_allocations: List[EquipmentAllocation] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)
    failure_criteria: List[str] = Field(default_factory=list)
    requirement_ids: List[str] = Field(default_factory=list)
    source_chunk_ids: List[str] = Field(default_factory=list)
    knowledge_unit_ids: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    need_human_confirm: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
