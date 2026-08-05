"""Requirement domain schemas."""

from typing import Any, List

from pydantic import BaseModel, ConfigDict, Field


class Requirement(BaseModel):
    """Canonical project requirement with structure and traceability."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    title: str
    description: str
    category: str = "功能需求"
    requirement_type: str = "functional"
    section_number: str = ""
    section_path: List[str] = Field(default_factory=list)
    test_object: str = ""
    actors: List[str] = Field(default_factory=list)
    preconditions: List[str] = Field(default_factory=list)
    inputs: List[str] = Field(default_factory=list)
    processing_rules: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    exception_rules: List[str] = Field(default_factory=list)
    performance_constraints: List[str] = Field(default_factory=list)
    interface_constraints: List[str] = Field(default_factory=list)
    security_constraints: List[str] = Field(default_factory=list)
    acceptance_criteria: List[str] = Field(default_factory=list)
    priority: str = ""
    verification_method: str = ""
    parent_requirement_ids: List[str] = Field(default_factory=list)
    recommended_test_type: str = ""
    alternative_test_types: List[str] = Field(default_factory=list)
    test_type_confidence: float = 0.0
    test_type_reasons: List[str] = Field(default_factory=list)
    source_chunk_ids: List[str] = Field(default_factory=list)
    source_documents: List[str] = Field(default_factory=list)
    source_evidence: List[dict[str, Any]] = Field(default_factory=list)
    need_human_confirm: bool = False
    missing_information: List[str] = Field(default_factory=list)


class RequirementItem(BaseModel):
    """Legacy requirement input retained as a compatibility schema."""

    model_config = ConfigDict(extra="ignore")

    requirement_id: str = ""
    requirement_text: str = ""
    test_object: str = ""
    scenario_name: str = ""
    scenario_environment: str = ""
    initial_condition: str = ""
    trigger_event: str = ""
    expected_behavior: str = ""
    evaluation_metrics: str = ""
    six_quality_attribute: List[str] = Field(default_factory=list)
    priority: str = "中"
    source_type: str = "requirement"
