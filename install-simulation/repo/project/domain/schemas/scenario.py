"""Scenario domain schemas."""

from typing import List

from pydantic import BaseModel, ConfigDict, Field


class Scenario(BaseModel):
    """Canonical project-scoped scenario grounded in requirements and chunks."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    project_id: str
    title: str
    requirement_ids: List[str] = Field(default_factory=list)
    preconditions: List[str] = Field(default_factory=list)
    trigger: str = ""
    normal_flow: List[str] = Field(default_factory=list)
    abnormal_flow: List[str] = Field(default_factory=list)
    source_chunk_ids: List[str] = Field(default_factory=list)
    source_documents: List[str] = Field(default_factory=list)
    need_human_confirm: bool = False
    missing_information: List[str] = Field(default_factory=list)


class ScenarioItem(BaseModel):
    """Legacy single-requirement scenario compatibility schema."""

    model_config = ConfigDict(extra="ignore")

    scenario_id: str = ""
    requirement_id: str = ""
    scenario_name: str = ""
    scenario_environment: str = ""
    initial_condition: str = ""
    trigger_event: str = ""
    expected_behavior: str = ""
    evaluation_metrics: str = ""
    test_object: str = ""
    six_quality_attribute: List[str] = Field(default_factory=list)


class ScenarioCard(BaseModel):
    """Legacy scenario-card persistence shape retained for current callers."""

    model_config = ConfigDict(extra="ignore")

    scenario_id: str = ""
    project_id: str = ""
    scenario_name: str = ""
    scenario_type: str = "业务场景"
    related_requirements: List[str] = Field(default_factory=list)
    actors: List[str] = Field(default_factory=list)
    preconditions: List[str] = Field(default_factory=list)
    trigger_event: str = ""
    input_data: List[str] = Field(default_factory=list)
    system_state: str = ""
    external_interfaces: List[str] = Field(default_factory=list)
    environment: List[str] = Field(default_factory=list)
    normal_flow: List[str] = Field(default_factory=list)
    abnormal_flow: List[str] = Field(default_factory=list)
    boundary_conditions: List[str] = Field(default_factory=list)
    performance_constraints: List[str] = Field(default_factory=list)
    safety_constraints: List[str] = Field(default_factory=list)
    source_document: List[str] = Field(default_factory=list)
    source_chunk_ids: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    need_human_confirm: bool = True


class ScenarioExtractionOutput(BaseModel):
    """Exact structured output requested from the scenario extraction model."""

    model_config = ConfigDict(extra="forbid")

    scenario_cards: List[ScenarioCard] = Field(default_factory=list)


class ScenarioExtractionResult(BaseModel):
    """Scenario extraction result with explicit provenance of generation mode."""

    model_config = ConfigDict(extra="forbid")

    scenario_cards: List[ScenarioCard] = Field(default_factory=list)
    generation_mode: str
    failure_type: str = ""
    failure_message: str = ""
