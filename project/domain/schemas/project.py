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


class ExtractedProjectProfile(BaseModel):
    """Model-facing profile fields without service-owned generation metadata."""

    model_config = ConfigDict(extra="forbid")

    project_name: str = Field(
        default="", description="项目资料中明确给出的项目或系统名称"
    )
    domain: str = Field(
        default="", description="项目所属业务或应用领域；资料未说明时留空"
    )
    test_object: str = Field(default="", description="被测试的软件、系统或设备名称")
    main_functions: List[str] = Field(
        default_factory=list, description="资料明确描述的主要功能"
    )
    interfaces: List[str] = Field(
        default_factory=list, description="资料明确描述的接口、协议或外部系统"
    )
    quality_attributes: List[str] = Field(
        default_factory=list,
        description="资料明确描述的性能、安全、可靠性等质量要求",
    )
    constraints: List[str] = Field(
        default_factory=list,
        description="资料明确描述的环境、部署、资源或判定约束",
    )


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
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    need_human_confirm: bool = True
    raw_response: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""
