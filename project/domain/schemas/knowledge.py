"""Structured project knowledge and review schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class DocumentSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str
    project_id: str
    document_id: str
    title: str = ""
    section_path: List[str] = Field(default_factory=list)
    content: str = ""
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    chunk_ids: List[str] = Field(default_factory=list)
    sequence_no: int = 0


class KnowledgeUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_unit_id: str
    project_id: str
    knowledge_type: str
    title: str = ""
    content: str
    normalized_data: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)
    document_id: str = ""
    chunk_ids: List[str] = Field(default_factory=list)
    page_no: Optional[int] = None
    jsonl_record_no: Optional[int] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    need_human_confirm: bool = False
    applicable_conditions: List[str] = Field(default_factory=list)
    inapplicable_conditions: List[str] = Field(default_factory=list)
    section_scope: str = ""
    status: Literal["draft", "reviewed", "approved", "rejected", "deprecated"] = "draft"
    source_kind: str = "book"
    priority: int = 0


KnowledgeType = Literal[
    "terminology", "subsystem", "simulation_model", "state_variable",
    "input_output", "state_transition", "environment_factor", "fault_mode",
    "parameter", "constraint", "verification_rule", "scenario_pattern",
]


class ParameterKnowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    symbol: str = ""
    value: Any = None
    unit: str = ""
    valid_range: str = ""
    operating_condition: str = ""
    related_model: str = ""
    value_type: Literal["示例值", "理论值", "试验值", "项目值"]
    source_page: Optional[int] = None


class ExtractedKnowledgeUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_type: KnowledgeType
    title: str
    content: str
    normalized_data: Dict[str, Any] = Field(default_factory=dict)
    parameter: Optional[ParameterKnowledge] = None
    tags: List[str] = Field(default_factory=list)
    applicable_conditions: List[str] = Field(default_factory=list)
    inapplicable_conditions: List[str] = Field(default_factory=list)
    source_chunk_ids: List[str] = Field(default_factory=list)
    source_page: Optional[int] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class KnowledgeExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_units: List[ExtractedKnowledgeUnit] = Field(default_factory=list)


class KnowledgeConflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_id: str
    project_id: str
    knowledge_unit_ids: List[str] = Field(default_factory=list)
    conflict_type: str
    description: str = ""
    status: str = "open"
    resolution: str = ""
    source_chunk_ids: List[str] = Field(default_factory=list)
    priority_winner_id: str = ""


class KnowledgeReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_id: str
    project_id: str
    knowledge_unit_id: str
    status: str
    reviewer: str = ""
    comments: List[str] = Field(default_factory=list)
    corrections: Dict[str, Any] = Field(default_factory=dict)
    reviewed_at: str = ""
    previous_status: str = ""
    new_status: str = ""
    action: str = ""
