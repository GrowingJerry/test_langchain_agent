"""Compatibility exports for legacy imports; new code should use ``domain.schemas``."""

from typing import Any, Dict

from domain.schemas import (
    DocGenerationTask,
    LibraryCaseItem,
    PlaceholderRow,
    RequirementItem,
    RetrievedLibraryCaseRow,
    ReviewResult,
    ScenarioCard,
    ScenarioItem,
    TestCaseItem,
)


def requirement_to_dict(requirement: RequirementItem) -> Dict[str, Any]:
    """Serialize a legacy requirement in its existing Excel-friendly shape."""
    data = requirement.model_dump()
    data["six_quality_attribute"] = ",".join(requirement.six_quality_attribute)
    return data


def test_case_to_dict(test_case: TestCaseItem) -> Dict[str, Any]:
    """Serialize a compatibility test case using canonical field names only."""
    return test_case.to_persistence_dict()


__all__ = [
    "DocGenerationTask",
    "LibraryCaseItem",
    "PlaceholderRow",
    "RequirementItem",
    "RetrievedLibraryCaseRow",
    "ReviewResult",
    "ScenarioCard",
    "ScenarioItem",
    "TestCaseItem",
    "requirement_to_dict",
    "test_case_to_dict",
]
