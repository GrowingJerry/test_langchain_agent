"""Structured input and output schemas for the test-case Agent."""

from typing import List

from pydantic import BaseModel, ConfigDict, Field

from domain.schemas.test_case import TestCase


class TestCaseAgentRequest(BaseModel):
    """One bounded request to generate project-grounded test cases."""

    __test__ = False
    model_config = ConfigDict(extra="forbid")

    requirement_ids: List[str] = Field(min_length=1)
    case_count: int = Field(default=1, ge=1, le=20)
    case_type: str = "功能测试"
    additional_instructions: str = ""


class GeneratedCaseBundle(BaseModel):
    """Only valid final output of the test-case Agent."""

    model_config = ConfigDict(extra="forbid")

    cases: List[TestCase] = Field(min_length=1)
    overall_missing_information: List[str] = Field(default_factory=list)
    used_tool_names: List[str] = Field(default_factory=list)
    retrieved_source_chunk_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
