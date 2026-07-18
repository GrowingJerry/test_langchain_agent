"""Canonical domain schemas and legacy compatibility exports."""

from domain.schemas.project import Project, ProjectAsset, ProjectProfile, VisualEvidence
from domain.schemas.retrieval import ProjectChunkResult
from domain.schemas.requirement import Requirement, RequirementItem
from domain.schemas.review import Review, ReviewResult
from domain.schemas.scenario import (
    Scenario,
    ScenarioCard,
    ScenarioExtractionOutput,
    ScenarioExtractionResult,
    ScenarioItem,
)
from domain.schemas.test_case import (
    DocGenerationTask,
    LibraryCaseItem,
    PlaceholderRow,
    RetrievedLibraryCaseRow,
    TestCase,
    TestCaseItem,
)

__all__ = [
    "DocGenerationTask",
    "LibraryCaseItem",
    "PlaceholderRow",
    "Project",
    "ProjectAsset",
    "ProjectChunkResult",
    "ProjectProfile",
    "Requirement",
    "RequirementItem",
    "RetrievedLibraryCaseRow",
    "Review",
    "ReviewResult",
    "Scenario",
    "ScenarioCard",
    "ScenarioExtractionOutput",
    "ScenarioExtractionResult",
    "ScenarioItem",
    "TestCase",
    "TestCaseItem",
    "VisualEvidence",
]
