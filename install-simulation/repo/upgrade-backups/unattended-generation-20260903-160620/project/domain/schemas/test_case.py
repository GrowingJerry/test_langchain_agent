"""Canonical test-case schema plus explicit legacy adapters."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)


def _string_list(value: Any) -> List[str]:
    """Normalize a scalar or collection to a clean string list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


class StructuredExpectedResult(BaseModel):
    page_change: str = ""
    element_change: str = ""
    visible_message: str = ""
    data_change: str = ""
    online_confirmation: str = ""


class StructuredTestStep(BaseModel):
    step_no: int = Field(ge=1)
    page_id: str = ""
    page_name: str = ""
    region: str = ""
    element_id: str = ""
    element_name: str = ""
    element_type: str = ""
    action: str = Field(min_length=1)
    input_value: str = ""
    instruction: str = Field(min_length=1)
    expected_result: StructuredExpectedResult
    evidence_source: str = ""
    binding_status: str = ""
    selection_reason: str = ""
    quality_issues: List[Dict[str, Any]] = Field(default_factory=list)
    need_human_confirm: bool = False


class TestCase(BaseModel):
    """Canonical generated test case used by all new code."""

    __test__ = False
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    case_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    preconditions: List[str] = Field(default_factory=list)
    test_steps: List[str] = Field(min_length=1)
    expected_results: List[str] = Field(min_length=1)
    evaluation_criteria: str = Field(min_length=1)
    test_data: List[str] = Field(default_factory=list)
    environment: List[str] = Field(default_factory=list)
    requirement_ids: List[str] = Field(default_factory=list)
    scenario_ids: List[str] = Field(default_factory=list)
    source_chunk_ids: List[str] = Field(default_factory=list)
    source_documents: List[str] = Field(default_factory=list)
    quality_category: List[str] = Field(default_factory=list)
    test_method: str = ""
    need_human_confirm: bool = False
    missing_information: List[str] = Field(default_factory=list)
    generation_mode: str = "unknown"
    indicator_ids: List[str] = Field(default_factory=list)
    atomic_requirement_id: str = ""
    test_point_id: str = ""
    test_point_scenario_type: str = "other"
    data_variant: str = ""
    scenario_variant: str = ""
    generation_batch_id: str = ""
    requirement_hierarchy_path: List[str] = Field(default_factory=list)
    function_id: str = ""
    page_ids: List[str] = Field(default_factory=list)
    html_element_ids: List[str] = Field(default_factory=list)
    playwright_observation_ids: List[str] = Field(default_factory=list)
    expected_source: str = "inferred_pending_confirmation"
    offline_verifiable: bool = False
    online_verification_items: List[str] = Field(default_factory=list)
    structured_steps: List[StructuredTestStep] = Field(default_factory=list)
    review_status: Literal["ready", "draft_needs_review", "accepted", "inactive"] = "ready"
    quality_issues: List[Dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _read_legacy_names(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        aliases = {
            "case_name": "title",
            "test_purpose": "objective",
            "expected_result": "expected_results",
            "pass_criteria": "evaluation_criteria",
            "test_input": "test_data",
            "input_data": "test_data",
            "test_environment": "environment",
            "scenario_environment": "environment",
            "requirement_id": "requirement_ids",
            "scenario_id": "scenario_ids",
            "source_document": "source_documents",
            "six_quality_attribute": "quality_category",
            "case_type": "quality_category",
            "need_human_confirmation": "need_human_confirm",
        }
        for legacy_name, canonical_name in aliases.items():
            if canonical_name not in data and legacy_name in data:
                data[canonical_name] = data[legacy_name]
        if "preconditions" not in data:
            for name in ("prerequisites", "initial_condition", "test_condition"):
                if data.get(name):
                    data["preconditions"] = data[name]
                    break
        return data

    @field_validator(
        "preconditions",
        "test_steps",
        "expected_results",
        "test_data",
        "environment",
        "requirement_ids",
        "scenario_ids",
        "source_chunk_ids",
        "source_documents",
        "quality_category",
        "missing_information",
        "indicator_ids", "requirement_hierarchy_path", "page_ids", "html_element_ids",
        "playwright_observation_ids", "online_verification_items",
        mode="before",
    )
    @classmethod
    def _normalize_lists(cls, value: Any) -> List[str]:
        return _string_list(value)

    @model_validator(mode="after")
    def _mark_missing_information(self) -> "TestCase":
        deduplicated = list(dict.fromkeys(self.missing_information))
        object.__setattr__(self, "missing_information", deduplicated)
        if deduplicated:
            object.__setattr__(self, "need_human_confirm", True)
        return self

    @classmethod
    def from_legacy_dict(cls, value: Mapping[str, Any]) -> "TestCase":
        """Validate a legacy dictionary and return a canonical test case."""
        return cls.model_validate(dict(value))

    def to_persistence_dict(self) -> Dict[str, Any]:
        """Return only canonical persistence fields."""
        return self.model_dump()


class TestCaseItem(TestCase):
    """Compatibility adapter for legacy callers; new code must use ``TestCase``."""

    case_id: str = ""
    title: str = ""
    objective: str = ""
    test_steps: List[str] = Field(default_factory=list)
    expected_results: List[str] = Field(default_factory=list)
    evaluation_criteria: str = ""
    _legacy: Dict[str, Any] = PrivateAttr(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        legacy_names = {
            "requirement_text",
            "requirement_tracking",
            "test_object",
            "scenario_name",
            "initial_condition",
            "trigger_event",
            "test_type",
            "test_basis",
            "test_condition",
            "actual_results",
            "pass_status",
            "issues_and_suggestions",
            "record_items",
            "test_result_template",
            "related_library_cases",
            "test_time",
            "test_location",
            "remarks",
            "review_score",
            "review_status",
            "suggestions",
            "assumptions_and_constraints",
        }
        captured = {name: data.get(name) for name in legacy_names if name in data}
        super().__init__(**data)
        self._legacy.update(captured)

    def _get_legacy(self, name: str, default: Any = "") -> Any:
        return self._legacy.get(name, default)

    def _set_legacy(self, name: str, value: Any) -> None:
        self._legacy[name] = value

    @property
    def case_name(self) -> str:
        return self.title

    @case_name.setter
    def case_name(self, value: str) -> None:
        self.title = value

    @property
    def test_purpose(self) -> str:
        return self.objective

    @test_purpose.setter
    def test_purpose(self, value: str) -> None:
        self.objective = value

    @property
    def expected_result(self) -> str:
        return "\n".join(self.expected_results)

    @expected_result.setter
    def expected_result(self, value: Any) -> None:
        self.expected_results = _string_list(value)

    @property
    def pass_criteria(self) -> str:
        return self.evaluation_criteria

    @pass_criteria.setter
    def pass_criteria(self, value: str) -> None:
        self.evaluation_criteria = value

    @property
    def requirement_id(self) -> str:
        return self.requirement_ids[0] if self.requirement_ids else ""

    @requirement_id.setter
    def requirement_id(self, value: str) -> None:
        self.requirement_ids = _string_list(value)

    @property
    def test_environment(self) -> str:
        return "；".join(self.environment)

    @test_environment.setter
    def test_environment(self, value: Any) -> None:
        self.environment = _string_list(value)

    @property
    def test_input(self) -> str:
        return "；".join(self.test_data)

    @test_input.setter
    def test_input(self, value: Any) -> None:
        self.test_data = _string_list(value)

    @property
    def six_quality_attribute(self) -> List[str]:
        return self.quality_category

    @six_quality_attribute.setter
    def six_quality_attribute(self, value: Any) -> None:
        self.quality_category = _string_list(value)

    @property
    def prerequisites(self) -> str:
        return "；".join(self.preconditions)

    @prerequisites.setter
    def prerequisites(self, value: Any) -> None:
        self.preconditions = _string_list(value)

    @property
    def source_document(self) -> str:
        return self.source_documents[0] if self.source_documents else ""

    @property
    def need_human_confirmation(self) -> bool:
        return self.need_human_confirm


def _legacy_property(name: str, default: Any = "") -> property:
    def getter(instance: TestCaseItem) -> Any:
        return instance._get_legacy(name, default() if callable(default) else default)

    def setter(instance: TestCaseItem, value: Any) -> None:
        instance._set_legacy(name, value)

    return property(getter, setter)


for _name, _default in {
    "requirement_text": "",
    "requirement_tracking": "",
    "test_object": "",
    "scenario_name": "",
    "scenario_environment": "",
    "initial_condition": "",
    "trigger_event": "",
    "test_type": "",
    "test_basis": "",
    "test_condition": "",
    "actual_results": "",
    "pass_status": "",
    "issues_and_suggestions": "",
    "record_items": list,
    "test_result_template": "",
    "related_library_cases": list,
    "test_time": "",
    "test_location": "",
    "remarks": "",
    "review_score": None,
    "review_status": "",
    "suggestions": "",
    "assumptions_and_constraints": "",
}.items():
    setattr(TestCaseItem, _name, _legacy_property(_name, _default))


class LibraryCaseItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    case_id: str = ""
    case_name: str = ""
    requirement_text: str = ""
    six_quality_attribute: str = ""
    test_object: str = ""
    test_type: str = ""
    test_method: str = ""
    test_condition: str = ""
    test_environment: str = ""
    test_steps: str = ""
    expected_result: str = ""
    pass_criteria: str = ""
    record_items: str = ""
    test_result_template: str = ""
    source_file: str = ""
    tags: str = ""


class PlaceholderRow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    doc_id: str = ""
    case_id: str = ""
    requirement_id: str = ""
    placeholder_name: str = ""
    placeholder_value: str = ""
    value_type: str = "text"
    source_sheet: str = "generated_test_cases"


class DocGenerationTask(BaseModel):
    model_config = ConfigDict(extra="ignore")
    doc_id: str = ""
    case_id: str = ""
    requirement_id: str = ""
    template_type: str = ""
    template_file: str = ""
    output_file: str = ""
    status: str = "pending"


class RetrievedLibraryCaseRow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    requirement_id: str = ""
    case_id: str = ""
    library_case_id: str = ""
    library_case_name: str = ""
    similarity_score: float = 0.0
    source_file: str = ""
    matched_reason: str = ""
