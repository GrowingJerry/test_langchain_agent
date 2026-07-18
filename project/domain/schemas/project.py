"""Project and project-owned asset schemas."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class Project(BaseModel):
    """A project boundary used to isolate all project-owned facts."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    project_name: str
    description: str = ""


class ProjectProfile(BaseModel):
    """Structured project profile grounded in current-project documents."""

    model_config = ConfigDict(extra="forbid")

    project_name: str = ""
    domain: str = ""
    test_object: str = ""
    main_functions: List[str] = Field(default_factory=list)
    interfaces: List[str] = Field(default_factory=list)
    quality_attributes: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    generation_mode: str = "structured_output"
    failure_type: str = ""
    failure_message: str = ""


class ProjectAsset(BaseModel):
    """A project-scoped visual or document-derived asset."""

    model_config = ConfigDict(extra="ignore")

    asset_id: str = ""
    project_id: str = ""
    document_id: str = ""
    asset_type: str = ""
    file_path: str = ""
    page_no: Optional[int] = None
    source_document: str = ""
    created_at: str = ""


class VisualEvidence(BaseModel):
    """Structured evidence extracted from a project image."""

    model_config = ConfigDict(extra="ignore")

    evidence_id: str = ""
    project_id: str = ""
    asset_id: str = ""
    image_type: str = ""
    main_objects: List[str] = Field(default_factory=list)
    visible_text: List[str] = Field(default_factory=list)
    possible_functions: List[str] = Field(default_factory=list)
    possible_test_points: List[str] = Field(default_factory=list)
    risk_points: List[str] = Field(default_factory=list)
    source_region: str = ""
    confidence: float = 0.0
    need_human_confirm: bool = True
    raw_response: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
