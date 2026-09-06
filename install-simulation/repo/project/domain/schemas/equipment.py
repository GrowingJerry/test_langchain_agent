"""Equipment-domain schemas used by configuration and scenario compilation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class EquipmentCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str
    equipment_id: str
    name: str
    description: str = ""
    capability_type: str = ""
    parameters: Dict[str, Any] = Field(default_factory=dict)
    constraints: List[str] = Field(default_factory=list)
    source_chunk_ids: List[str] = Field(default_factory=list)
    jsonl_record_no: Optional[int] = None


class EquipmentEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    equipment_id: str
    project_id: str
    name: str
    category: str = ""
    equipment_type: str = ""
    country: str = ""
    aliases: List[str] = Field(default_factory=list)
    attributes: Dict[str, Any] = Field(default_factory=dict)
    capabilities: List[EquipmentCapability] = Field(default_factory=list)
    source_document_id: str = ""
    source_chunk_ids: List[str] = Field(default_factory=list)
    source_page_no: Optional[int] = None
    jsonl_record_no: Optional[int] = None
    need_human_confirm: bool = False


class EquipmentRoleMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping_id: str
    project_id: str
    equipment_id: str
    role_name: str
    capability_ids: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    priority: int = 0
    source_chunk_ids: List[str] = Field(default_factory=list)


class EquipmentConfigurationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    project_id: str
    name: str
    description: str = ""
    condition_expression: str = ""
    required_roles: List[str] = Field(default_factory=list)
    required_capabilities: List[str] = Field(default_factory=list)
    compatible_equipment_types: List[str] = Field(default_factory=list)
    incompatible_equipment_ids: List[str] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)
    source_chunk_ids: List[str] = Field(default_factory=list)
    enabled: bool = True


class EquipmentMatch(BaseModel):
    """One deterministic equipment-match decision with explainable provenance."""

    model_config = ConfigDict(extra="forbid")

    equipment_id: str
    name: str
    matched_roles: List[str] = Field(default_factory=list)
    matched_capabilities: List[str] = Field(default_factory=list)
    missing_capabilities: List[str] = Field(default_factory=list)
    violated_constraints: List[str] = Field(default_factory=list)
    score: float = Field(default=0.0, ge=0.0, le=100.0)
    match_reason: str = ""
    source_refs: List[Dict[str, Any]] = Field(default_factory=list)
    need_human_confirm: bool = False


class EquipmentSearchResult(BaseModel):
    """Applicable matches plus rejected diagnostics and explicit missing information."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    matches: List[EquipmentMatch] = Field(default_factory=list)
    rejected_matches: List[EquipmentMatch] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    used_embedding: bool = False
