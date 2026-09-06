"""Review result schemas."""

from typing import List, Literal

from pydantic import BaseModel, ConfigDict, Field


class Review(BaseModel):
    """Canonical review result for one generated artifact."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    review_type: Literal["deterministic", "quality", "llm"] = "deterministic"
    status: Literal["passed", "needs_revision", "needs_human_confirmation"]
    score: float = 0.0
    issues: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    need_human_confirm: bool = False


class ReviewResult(BaseModel):
    """Legacy test-case review compatibility schema."""

    model_config = ConfigDict(extra="ignore")

    case_id: str = ""
    review_score: float = 0.0
    review_status: str = ""
    review_issues: List[str] = Field(default_factory=list)
    review_suggestions: List[str] = Field(default_factory=list)
