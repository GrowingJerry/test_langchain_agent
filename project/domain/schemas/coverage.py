"""Coverage-plan domain objects used between atomic requirements and test cases."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ScenarioType = Literal[
    "normal", "abnormal", "boundary", "state", "constraint", "recovery",
    "compatibility", "security", "performance", "reliability", "other",
]


class TestPoint(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="ignore")
    test_point_id: str = Field(min_length=1)
    atomic_requirement_id: str = Field(min_length=1)
    requirement_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    scenario_type: ScenarioType = "other"
    description: str = Field(min_length=1)
    priority: str = "P1"
    evidence_requirement: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoveragePlan(BaseModel):
    requirement_id: str
    test_points: list[TestPoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self) -> "CoveragePlan":
        ids = [x.test_point_id for x in self.test_points]
        if len(ids) != len(set(ids)):
            raise ValueError("coverage plan contains duplicate test_point_id")
        return self


class CoverageAudit(BaseModel):
    planned_test_point_ids: list[str]
    generated_test_point_ids: list[str]
    missing_test_point_ids: list[str]
    coverage_status: Literal["completed", "incomplete_coverage", "needs_review"]
