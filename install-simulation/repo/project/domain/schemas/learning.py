"""Schemas for incremental, project-scoped document workflows.learning."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field


class LearningTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    project_id: str
    task_type: str
    task_name: str = ""
    learning_goal: str = ""
    domain: str = ""
    simulation_object: str = ""
    target_subsystems: List[str] = Field(default_factory=list)
    target_topics: List[str] = Field(default_factory=list)
    expected_scenario_types: List[str] = Field(default_factory=list)
    excluded_topics: List[str] = Field(default_factory=list)
    status: str = "pending"
    document_ids: List[str] = Field(default_factory=list)
    processed_document_ids: List[str] = Field(default_factory=list)
    failed_document_ids: List[str] = Field(default_factory=list)
    progress_current: int = 0
    progress_total: int = 0
    error_messages: List[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class KnowledgeCoverageCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    chunk_id: str
    parent_section_id: str = ""
    section_title: str = ""
    page_start: int | None = None
    page_end: int | None = None
    content: str
    matched_topics: List[str] = Field(default_factory=list)
    relevance_score: float = Field(default=0.0, ge=0.0)
