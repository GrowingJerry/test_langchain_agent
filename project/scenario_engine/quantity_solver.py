"""Deterministic equipment quantity solver without expression evaluation."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from equipment.allocation_rules import (
    AllocationRule,
    AllocationRuleType,
    CapacityParameters,
    DependencyParameters,
    FixedParameters,
    InventoryLimitParameters,
    ManualOnlyParameters,
    MinimumParameters,
    MinMaxParameters,
    MutualExclusionParameters,
    RatioParameters,
    RedundancyParameters,
    RoundingMode,
    parameter_model,
)


class QuantitySolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    equipment_id: str
    quantity: Optional[int] = Field(default=None, ge=0)
    quantity_source: str = "unknown"
    rule_id: str = ""
    formula_description: str = ""
    input_values: Dict[str, Any] = Field(default_factory=dict)
    constraints_checked: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    conflicts: List[str] = Field(default_factory=list)
    source_refs: List[Dict[str, Any]] = Field(default_factory=list)
    need_human_confirm: bool = False


class QuantitySolver:
    """Solve rules in a stable topological order and report every conflict."""

    QUANTITY_RULES = {
        AllocationRuleType.FIXED,
        AllocationRuleType.MINIMUM,
        AllocationRuleType.CAPACITY,
        AllocationRuleType.RATIO,
        AllocationRuleType.REDUNDANCY,
        AllocationRuleType.MIN_MAX,
        AllocationRuleType.MANUAL_ONLY,
    }

    def solve(
        self,
        rules: List[AllocationRule],
        *,
        input_values: Optional[Dict[str, Any]] = None,
        inventory: Optional[Dict[str, int]] = None,
        manual_quantities: Optional[Dict[str, int]] = None,
        requested_allocations: Optional[List[Tuple[str, str]]] = None,
    ) -> List[QuantitySolution]:
        values = dict(input_values or {})
        stock = dict(inventory or {})
        manual = dict(manual_quantities or {})
        grouped: Dict[Tuple[str, str], List[AllocationRule]] = {}
        for rule in rules:
            if rule.enabled:
                grouped.setdefault((rule.role, rule.equipment_id), []).append(rule)
        for key in requested_allocations or []:
            grouped.setdefault(key, [])
        order, cycle_roles = self._topological_roles(grouped)
        role_quantities: Dict[str, int] = {}
        solutions: List[QuantitySolution] = []
        for role in order:
            for role_key in sorted(key for key in grouped if key[0] == role):
                solution = self._solve_one(
                    role_key,
                    self._ordered_rules(grouped[role_key]),
                    values,
                    stock,
                    manual,
                    role_quantities,
                )
                if role in cycle_roles:
                    solution.conflicts.append("角色依赖存在循环")
                    solution.warnings.append("冲突：角色依赖存在循环")
                    solution.need_human_confirm = True
                solutions.append(solution)
                if solution.quantity is not None and not solution.conflicts:
                    role_quantities[role] = role_quantities.get(role, 0) + solution.quantity
        self._check_mutual_exclusion(solutions, grouped)
        return solutions

    def _solve_one(
        self,
        key: Tuple[str, str],
        rules: List[AllocationRule],
        values: Dict[str, Any],
        stock: Dict[str, int],
        manual: Dict[str, int],
        role_quantities: Dict[str, int],
    ) -> QuantitySolution:
        role, equipment_id = key
        solution = QuantitySolution(role=role, equipment_id=equipment_id)
        if not rules:
            solution.warnings.append("没有数量规则，quantity保持为null")
            solution.need_human_confirm = True
            return solution
        solution.source_refs = self._source_refs(rules)
        manual_rules = [
            rule for rule in rules if rule.rule_type == AllocationRuleType.MANUAL_ONLY
        ]
        if manual_rules:
            rule = manual_rules[0]
            params = parameter_model(rule)
            assert isinstance(params, ManualOnlyParameters)
            solution.rule_id = rule.rule_id
            solution.quantity_source = AllocationRuleType.MANUAL_ONLY.value
            solution.formula_description = params.reason or "数量必须由人工明确指定"
            if role not in manual:
                solution.warnings.append("manual_only规则要求人工指定数量")
                solution.need_human_confirm = True
                return solution
            quantity = self._non_negative_int(manual[role], f"manual:{role}")
            solution.quantity = quantity
            solution.input_values[role] = quantity
        else:
            for rule in rules:
                if rule.rule_type in self.QUANTITY_RULES:
                    self._apply_quantity_rule(
                        solution, rule, values, role_quantities
                    )
        for rule in rules:
            self._apply_constraint_rule(
                solution, rule, stock, role_quantities
            )
        solution.need_human_confirm = bool(
            solution.need_human_confirm
            or solution.quantity is None
            or solution.conflicts
        )
        for conflict in solution.conflicts:
            warning = f"冲突：{conflict}"
            if warning not in solution.warnings:
                solution.warnings.append(warning)
        return solution

    @staticmethod
    def _ordered_rules(rules: List[AllocationRule]) -> List[AllocationRule]:
        precedence = {
            AllocationRuleType.FIXED: 0,
            AllocationRuleType.CAPACITY: 0,
            AllocationRuleType.RATIO: 0,
            AllocationRuleType.REDUNDANCY: 0,
            AllocationRuleType.MINIMUM: 1,
            AllocationRuleType.MIN_MAX: 2,
            AllocationRuleType.INVENTORY_LIMIT: 3,
            AllocationRuleType.DEPENDENCY: 3,
            AllocationRuleType.MUTUAL_EXCLUSION: 3,
            AllocationRuleType.MANUAL_ONLY: -1,
        }
        return sorted(rules, key=lambda rule: (precedence[rule.rule_type], rule.rule_id))

    def _apply_quantity_rule(
        self,
        solution: QuantitySolution,
        rule: AllocationRule,
        values: Dict[str, Any],
        role_quantities: Dict[str, int],
    ) -> None:
        params = parameter_model(rule)
        quantity: Optional[int] = solution.quantity
        description = ""
        used: Dict[str, Any] = {}
        if isinstance(params, FixedParameters):
            quantity = params.quantity
            description = f"固定数量={quantity} {params.unit}"
        elif isinstance(params, MinimumParameters):
            quantity = max(quantity or 0, params.minimum)
            description = f"数量不低于{params.minimum} {params.unit}"
        elif isinstance(params, CapacityParameters):
            demand = self._number(
                values, params.demand_variable, params.demand_unit
            )
            raw = demand / params.capacity_per_unit
            quantity = self._round(raw, params.rounding) + params.redundancy
            used[params.demand_variable] = demand
            description = (
                f"{params.demand_variable}({demand})/{params.capacity_per_unit}"
                f"按{params.rounding.value}取整+冗余{params.redundancy}"
            )
        elif isinstance(params, RatioParameters):
            base_name, base = self._base_value(params, values, role_quantities)
            quantity = self._round(base * params.ratio, params.rounding)
            used[base_name] = base
            description = f"{base_name}({base})×比例{params.ratio}按{params.rounding.value}取整"
        elif isinstance(params, RedundancyParameters):
            base_name, base = self._redundancy_base(params, values, role_quantities)
            quantity = base + params.redundancy
            used[base_name] = base
            description = f"{base_name}({base})+冗余{params.redundancy}"
        elif isinstance(params, MinMaxParameters):
            if params.source_variable:
                source = self._non_negative_int(
                    self._number(values, params.source_variable, params.unit),
                    params.source_variable,
                )
                quantity = source
                used[params.source_variable] = source
            elif quantity is None:
                quantity = params.minimum
            description = f"校验范围[{params.minimum},{params.maximum}] {params.unit}"
            if quantity < params.minimum or quantity > params.maximum:
                solution.conflicts.append(
                    f"数量{quantity}超出上下限[{params.minimum},{params.maximum}]"
                )
        if quantity is not None:
            solution.quantity = self._non_negative_int(quantity, rule.rule_id)
            solution.quantity_source = rule.rule_type.value
            solution.rule_id = rule.rule_id
            solution.formula_description = description
            solution.input_values.update(used)

    def _apply_constraint_rule(
        self,
        solution: QuantitySolution,
        rule: AllocationRule,
        stock: Dict[str, int],
        role_quantities: Dict[str, int],
    ) -> None:
        params = parameter_model(rule)
        if isinstance(params, InventoryLimitParameters):
            available = self._non_negative_int(
                stock.get(params.inventory_key or solution.equipment_id, 0),
                "inventory",
            )
            solution.constraints_checked.append(rule.rule_id)
            solution.input_values["inventory_available"] = available
            if solution.quantity is None:
                solution.warnings.append("库存限制不能单独推导需求数量")
            elif solution.quantity > available:
                solution.conflicts.append(
                    f"库存不足:需求{solution.quantity},可用{available}"
                )
        elif isinstance(params, DependencyParameters):
            solution.constraints_checked.append(rule.rule_id)
            for dependency in params.depends_on_roles:
                if role_quantities.get(dependency, 0) <= 0:
                    solution.conflicts.append(f"角色依赖未满足:{dependency}")
        elif isinstance(params, MutualExclusionParameters):
            solution.constraints_checked.append(rule.rule_id)

    @staticmethod
    def _number(
        values: Dict[str, Any], name: str, expected_unit: str = ""
    ) -> float:
        if name not in values:
            raise ValueError(f"missing numeric input: {name}")
        value = values[name]
        if isinstance(value, dict):
            actual_unit = str(value.get("unit") or "")
            if expected_unit and actual_unit != expected_unit:
                raise ValueError(
                    f"input {name} unit must be {expected_unit!r}, got {actual_unit!r}"
                )
            value = value.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"input {name} must be numeric")
        if not math.isfinite(float(value)) or value < 0:
            raise ValueError(f"input {name} must be finite and non-negative")
        return float(value)

    @staticmethod
    def _non_negative_int(value: Any, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be numeric")
        if not math.isfinite(float(value)) or value < 0 or int(value) != value:
            raise ValueError(f"{name} must be a non-negative integer")
        return int(value)

    @staticmethod
    def _round(value: float, mode: RoundingMode) -> int:
        if mode == RoundingMode.CEIL:
            return math.ceil(value)
        if mode == RoundingMode.FLOOR:
            return math.floor(value)
        return math.floor(value + 0.5)

    def _base_value(
        self,
        params: RatioParameters,
        values: Dict[str, Any],
        role_quantities: Dict[str, int],
    ) -> Tuple[str, float]:
        if params.base_variable:
            return params.base_variable, self._number(
                values, params.base_variable, params.base_unit
            )
        return params.related_role, float(role_quantities.get(params.related_role, 0))

    def _redundancy_base(
        self,
        params: RedundancyParameters,
        values: Dict[str, Any],
        role_quantities: Dict[str, int],
    ) -> Tuple[str, int]:
        if params.base_variable:
            return params.base_variable, self._non_negative_int(
                self._number(values, params.base_variable, params.base_unit),
                params.base_variable,
            )
        if params.related_role:
            return params.related_role, role_quantities.get(params.related_role, 0)
        return "base_quantity", params.base_quantity if params.base_quantity is not None else 1

    @staticmethod
    def _source_refs(rules: Iterable[AllocationRule]) -> List[Dict[str, Any]]:
        refs: List[Dict[str, Any]] = []
        seen: set[Tuple[Tuple[str, str], ...]] = set()
        for rule in rules:
            for ref in rule.source_refs:
                key = tuple(sorted((str(k), str(v)) for k, v in ref.items()))
                if key not in seen:
                    seen.add(key)
                    refs.append(dict(ref))
        return refs

    def _topological_roles(
        self, grouped: Dict[Tuple[str, str], List[AllocationRule]]
    ) -> Tuple[List[str], set[str]]:
        roles = {role for role, _ in grouped}
        dependencies: Dict[str, set[str]] = {role: set() for role in roles}
        for (role, _), rules in grouped.items():
            for rule in rules:
                if rule.rule_type == AllocationRuleType.DEPENDENCY:
                    params = parameter_model(rule)
                    assert isinstance(params, DependencyParameters)
                    dependencies[role].update(params.depends_on_roles)
                    roles.update(params.depends_on_roles)
        for role in roles:
            dependencies.setdefault(role, set())
        order: List[str] = []
        pending = {role: set(deps) for role, deps in dependencies.items()}
        while pending:
            ready = sorted(role for role, deps in pending.items() if not deps)
            if not ready:
                cycle_roles = set(pending)
                return [*order, *sorted(pending)], cycle_roles
            for role in ready:
                order.append(role)
                pending.pop(role)
            for deps in pending.values():
                deps.difference_update(ready)
        return order, set()

    def _check_mutual_exclusion(
        self,
        solutions: List[QuantitySolution],
        grouped: Dict[Tuple[str, str], List[AllocationRule]],
    ) -> None:
        active_roles = {
            solution.role for solution in solutions if (solution.quantity or 0) > 0
        }
        active_equipment = {
            solution.equipment_id for solution in solutions if (solution.quantity or 0) > 0
        }
        by_key = {(solution.role, solution.equipment_id): solution for solution in solutions}
        for key, rules in grouped.items():
            solution = by_key.get(key)
            if solution is None or (solution.quantity or 0) <= 0:
                continue
            for rule in rules:
                if rule.rule_type != AllocationRuleType.MUTUAL_EXCLUSION:
                    continue
                params = parameter_model(rule)
                assert isinstance(params, MutualExclusionParameters)
                conflicts = sorted(
                    set(params.excluded_roles) & active_roles
                ) + sorted(set(params.excluded_equipment_ids) & active_equipment)
                if conflicts:
                    solution.conflicts.append(
                        "互斥配置冲突:" + "、".join(conflicts)
                    )
                    solution.warnings.append("冲突：互斥配置同时启用")
                    solution.need_human_confirm = True
