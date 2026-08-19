"""Structured input and output schemas for the test-case Agent."""

from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, model_validator

from domain.schemas.test_case import TestCase


class TestCaseAgentRequest(BaseModel):
    """One bounded request to generate project-grounded test cases."""

    __test__ = False
    model_config = ConfigDict(extra="forbid")

    requirement_ids: List[str] = Field(default_factory=list)
    scenario_ids: List[str] = Field(default_factory=list)
    case_count: int = Field(default=1, ge=1, le=100)
    auto_case_count: bool = False
    case_type: str = "功能测试"
    additional_instructions: str = ""

    @model_validator(mode="after")
    def require_grounded_target(self) -> "TestCaseAgentRequest":
        if not self.requirement_ids and not self.scenario_ids:
            raise ValueError("requirement_ids or scenario_ids is required")
        return self


class GeneratedCaseBundle(BaseModel):
    """Only valid final output of the test-case Agent."""

    model_config = ConfigDict(extra="forbid")

    cases: List[TestCase] = Field(min_length=1)
    overall_missing_information: List[str] = Field(default_factory=list)
    used_tool_names: List[str] = Field(default_factory=list)
    retrieved_source_chunk_ids: List[str] = Field(default_factory=list)
    knowledge_unit_ids: List[str] = Field(default_factory=list)
    equipment_ids: List[str] = Field(default_factory=list)
    configuration_rule_ids: List[str] = Field(default_factory=list)
    scenario_validation_run_id: str = ""
    warnings: List[str] = Field(default_factory=list)
    coverage_plan: List[Dict[str, Any]] = Field(default_factory=list)
    coverage_result: Dict[str, Any] = Field(default_factory=dict)
