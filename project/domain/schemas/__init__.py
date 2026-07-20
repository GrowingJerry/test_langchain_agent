"""Canonical domain schemas and legacy compatibility exports."""

from domain.schemas.allocation import EquipmentAllocation, ScenarioValidationResult
from domain.schemas.equipment import (
    EquipmentCapability,
    EquipmentConfigurationRule,
    EquipmentEntity,
    EquipmentMatch,
    EquipmentRoleMapping,
    EquipmentSearchResult,
)
from domain.schemas.knowledge import (
    DocumentSection,
    ExtractedKnowledgeUnit,
    KnowledgeConflict,
    KnowledgeExtractionOutput,
    KnowledgeReview,
    KnowledgeUnit,
    ParameterKnowledge,
)
from domain.schemas.learning import KnowledgeCoverageCandidate, LearningTask
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
from domain.schemas.scenario_spec import (
    ScenarioIntent,
    ScenarioRoleRequirement,
    ScenarioSpec,
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
    "DocumentSection",
    "ExtractedKnowledgeUnit",
    "EquipmentAllocation",
    "EquipmentCapability",
    "EquipmentConfigurationRule",
    "EquipmentEntity",
    "EquipmentMatch",
    "EquipmentRoleMapping",
    "EquipmentSearchResult",
    "KnowledgeConflict",
    "KnowledgeCoverageCandidate",
    "KnowledgeExtractionOutput",
    "KnowledgeReview",
    "KnowledgeUnit",
    "LearningTask",
    "ParameterKnowledge",
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
    "ScenarioIntent",
    "ScenarioItem",
    "ScenarioRoleRequirement",
    "ScenarioSpec",
    "ScenarioValidationResult",
    "TestCase",
    "TestCaseItem",
    "VisualEvidence",
]
