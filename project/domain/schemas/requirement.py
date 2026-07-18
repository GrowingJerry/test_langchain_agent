"""Requirement domain schemas."""

from typing import List

from pydantic import BaseModel, ConfigDict, Field


class Requirement(BaseModel):
    """Canonical project requirement with traceability."""

    model_config = ConfigDict(extra="forbid")

    requirement_id: str
    title: str
    description: str
    category: str = "功能需求"
    source_chunk_ids: List[str] = Field(default_factory=list)
    source_documents: List[str] = Field(default_factory=list)
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
