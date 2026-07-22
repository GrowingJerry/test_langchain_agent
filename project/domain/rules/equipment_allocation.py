"""Strict, non-executable rule definitions for deterministic allocation."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AllocationRuleType(str, Enum):
    FIXED = "fixed"
    MINIMUM = "minimum"
    CAPACITY = "capacity"
    RATIO = "ratio"
    REDUNDANCY = "redundancy"
    MIN_MAX = "min_max"
    INVENTORY_LIMIT = "inventory_limit"
    DEPENDENCY = "dependency"
    MUTUAL_EXCLUSION = "mutual_exclusion"
    MANUAL_ONLY = "manual_only"


class RoundingMode(str, Enum):
    CEIL = "ceil"
    FLOOR = "floor"
    NEAREST = "nearest"


class _Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FixedParameters(_Parameters):
    quantity: int = Field(ge=0)
    unit: str = Field(default="entity", min_length=1)


class MinimumParameters(_Parameters):
    minimum: int = Field(ge=0)
    unit: str = Field(default="entity", min_length=1)


class CapacityParameters(_Parameters):
    demand_variable: str = Field(min_length=1)
    capacity_per_unit: float = Field(gt=0)
    rounding: RoundingMode = RoundingMode.CEIL
    redundancy: int = Field(default=0, ge=0)
    demand_unit: str = Field(default="target", min_length=1)
    quantity_unit: str = Field(default="entity", min_length=1)


class RatioParameters(_Parameters):
    ratio: float = Field(ge=0)
    base_variable: str = ""
    related_role: str = ""
    rounding: RoundingMode = RoundingMode.CEIL
    base_unit: str = Field(default="entity", min_length=1)
    quantity_unit: str = Field(default="entity", min_length=1)

    @model_validator(mode="after")
    def require_base(self) -> "RatioParameters":
        if not self.base_variable and not self.related_role:
            raise ValueError("ratio requires base_variable or related_role")
        return self


class RedundancyParameters(_Parameters):
    redundancy: int = Field(default=1, ge=0)
    base_quantity: Optional[int] = Field(default=None, ge=0)
    base_variable: str = ""
    related_role: str = ""
    base_unit: str = Field(default="entity", min_length=1)
    quantity_unit: str = Field(default="entity", min_length=1)


class MinMaxParameters(_Parameters):
    minimum: int = Field(ge=0)
    maximum: int = Field(ge=0)
    source_variable: str = ""
    unit: str = Field(default="entity", min_length=1)

    @model_validator(mode="after")
    def validate_range(self) -> "MinMaxParameters":
        if self.maximum < self.minimum:
            raise ValueError("maximum must be greater than or equal to minimum")
        return self


class InventoryLimitParameters(_Parameters):
    inventory_key: str = ""
    unit: str = Field(default="entity", min_length=1)


class DependencyParameters(_Parameters):
    depends_on_roles: List[str] = Field(min_length=1)


class MutualExclusionParameters(_Parameters):
    excluded_roles: List[str] = Field(default_factory=list)
    excluded_equipment_ids: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_exclusion(self) -> "MutualExclusionParameters":
        if not self.excluded_roles and not self.excluded_equipment_ids:
            raise ValueError(
                "mutual_exclusion requires excluded_roles or excluded_equipment_ids"
            )
        return self


class ManualOnlyParameters(_Parameters):
    reason: str = ""
    unit: str = Field(default="entity", min_length=1)


PARAMETER_MODELS: Dict[AllocationRuleType, Type[_Parameters]] = {
    AllocationRuleType.FIXED: FixedParameters,
    AllocationRuleType.MINIMUM: MinimumParameters,
    AllocationRuleType.CAPACITY: CapacityParameters,
    AllocationRuleType.RATIO: RatioParameters,
    AllocationRuleType.REDUNDANCY: RedundancyParameters,
    AllocationRuleType.MIN_MAX: MinMaxParameters,
    AllocationRuleType.INVENTORY_LIMIT: InventoryLimitParameters,
    AllocationRuleType.DEPENDENCY: DependencyParameters,
    AllocationRuleType.MUTUAL_EXCLUSION: MutualExclusionParameters,
    AllocationRuleType.MANUAL_ONLY: ManualOnlyParameters,
}


class AllocationRule(BaseModel):
    """A validated declarative rule; parameters never contain executable formulas."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    role: str
    equipment_id: str
    rule_type: AllocationRuleType
    parameters: Dict[str, Any] = Field(default_factory=dict)
    source_refs: List[Dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_parameters(self) -> "AllocationRule":
        validated = PARAMETER_MODELS[self.rule_type].model_validate(self.parameters)
        self.parameters = validated.model_dump(mode="json")
        return self


def parameter_model(rule: AllocationRule) -> _Parameters:
    return PARAMETER_MODELS[rule.rule_type].model_validate(rule.parameters)
