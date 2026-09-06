"""Equipment allocation and scenario-validation schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class EquipmentAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allocation_id: str
    project_id: str
    scenario_id: str
    role_requirement_id: str
    equipment_id: str
    quantity: Optional[int] = Field(default=1, ge=0)
    configuration: Dict[str, Any] = Field(default_factory=dict)
    capability_ids: List[str] = Field(default_factory=list)
    rule_ids: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    source_chunk_ids: List[str] = Field(default_factory=list)
    source_refs: List[Dict[str, Any]] = Field(default_factory=list)
    need_human_confirm: bool = False


class ScenarioValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    validation_id: str
    project_id: str
    scenario_id: str
    is_valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    missing_roles: List[str] = Field(default_factory=list)
    conflicting_rule_ids: List[str] = Field(default_factory=list)
    checked_allocation_ids: List[str] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
    need_human_confirm: bool = False
    passed: bool = False
    score: float = Field(default=0.0, ge=0.0, le=100.0)
    blocking_issues: List[str] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    check_results: Dict[str, Any] = Field(default_factory=dict)
