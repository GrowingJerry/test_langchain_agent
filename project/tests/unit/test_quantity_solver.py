from __future__ import annotations

from typing import Any, Dict

import pytest
from pydantic import ValidationError

from domain.rules.equipment_allocation import AllocationRule
from workflows.scenario.quantity_solver import QuantitySolver


def _rule(
    rule_id: str,
    role: str,
    equipment_id: str,
    rule_type: str,
    parameters: Dict[str, Any],
) -> AllocationRule:
    return AllocationRule(
        rule_id=rule_id,
        role=role,
        equipment_id=equipment_id,
        rule_type=rule_type,
        parameters=parameters,
        source_refs=[{"document_id": "DOC-1", "chunk_id": "CHK-1", "page_no": 2}],
    )


def test_fixed_rule() -> None:
    result = QuantitySolver().solve(
        [_rule("R-FIX", "目标", "EQ-1", "fixed", {"quantity": 3})]
    )[0]
    assert result.quantity == 3
    assert result.quantity_source == "fixed"
    assert result.rule_id == "R-FIX"
    assert "固定数量=3" in result.formula_description
    assert result.source_refs[0]["chunk_id"] == "CHK-1"


def test_minimum_rule() -> None:
    result = QuantitySolver().solve(
        [_rule("R-MIN", "值守", "EQ-1", "minimum", {"minimum": 2})]
    )[0]
    assert result.quantity == 2
    assert result.quantity_source == "minimum"


def test_capacity_rule_uses_explicit_rounding_and_redundancy() -> None:
    result = QuantitySolver().solve(
        [
            _rule(
                "R-CAP",
                "处理节点",
                "EQ-1",
                "capacity",
                {
                    "demand_variable": "target_count",
                    "capacity_per_unit": 4,
                    "rounding": "ceil",
                    "redundancy": 1,
                },
            )
        ],
        input_values={"target_count": 10},
    )[0]
    assert result.quantity == 4
    assert result.input_values == {"target_count": 10.0}
    assert "10.0" in result.formula_description


def test_capacity_rule_validates_explicit_input_unit() -> None:
    rule = _rule(
        "R-CAP",
        "处理节点",
        "EQ-1",
        "capacity",
        {
            "demand_variable": "target_count",
            "capacity_per_unit": 4,
            "demand_unit": "target",
        },
    )
    valid = QuantitySolver().solve(
        [rule], input_values={"target_count": {"value": 8, "unit": "target"}}
    )[0]
    assert valid.quantity == 2
    with pytest.raises(ValueError, match="unit must be"):
        QuantitySolver().solve(
            [rule], input_values={"target_count": {"value": 8, "unit": "person"}}
        )


def test_ratio_rule() -> None:
    result = QuantitySolver().solve(
        [
            _rule(
                "R-RATIO",
                "伴随节点",
                "EQ-1",
                "ratio",
                {"base_variable": "target_count", "ratio": 0.5, "rounding": "ceil"},
            )
        ],
        input_values={"target_count": 5},
    )[0]
    assert result.quantity == 3
    assert result.quantity_source == "ratio"


def test_redundancy_rule() -> None:
    result = QuantitySolver().solve(
        [
            _rule(
                "R-REDUNDANCY",
                "主备服务",
                "EQ-1",
                "redundancy",
                {"base_quantity": 2, "redundancy": 1},
            )
        ]
    )[0]
    assert result.quantity == 3
    assert "冗余1" in result.formula_description


def test_min_max_rule_validates_bounds_without_silent_clamping() -> None:
    rule = _rule(
        "R-RANGE",
        "终端",
        "EQ-1",
        "min_max",
        {"minimum": 2, "maximum": 5, "source_variable": "requested"},
    )
    valid = QuantitySolver().solve([rule], input_values={"requested": 4})[0]
    conflict = QuantitySolver().solve([rule], input_values={"requested": 7})[0]
    assert valid.quantity == 4
    assert valid.conflicts == []
    assert conflict.quantity == 7
    assert "超出上下限" in conflict.conflicts[0]
    assert conflict.need_human_confirm is True


def test_inventory_limit_reports_conflict_without_reducing_quantity() -> None:
    rules = [
        _rule("R-FIX", "节点", "EQ-1", "fixed", {"quantity": 5}),
        _rule("R-INV", "节点", "EQ-1", "inventory_limit", {}),
    ]
    result = QuantitySolver().solve(rules, inventory={"EQ-1": 3})[0]
    assert result.quantity == 5
    assert "库存不足:需求5,可用3" in result.conflicts
    assert "R-INV" in result.constraints_checked


def test_dependency_is_topologically_solved() -> None:
    rules = [
        _rule("R-CHILD", "业务节点", "EQ-2", "fixed", {"quantity": 1}),
        _rule(
            "R-DEP",
            "业务节点",
            "EQ-2",
            "dependency",
            {"depends_on_roles": ["基础节点"]},
        ),
        _rule("R-BASE", "基础节点", "EQ-1", "fixed", {"quantity": 1}),
    ]
    results = QuantitySolver().solve(rules)
    assert [item.role for item in results] == ["基础节点", "业务节点"]
    assert results[1].conflicts == []
    assert "R-DEP" in results[1].constraints_checked


def test_dependency_cycle_is_reported_deterministically() -> None:
    rules = [
        _rule("R-A", "A", "EQ-1", "fixed", {"quantity": 1}),
        _rule("R-A-D", "A", "EQ-1", "dependency", {"depends_on_roles": ["B"]}),
        _rule("R-B", "B", "EQ-2", "fixed", {"quantity": 1}),
        _rule("R-B-D", "B", "EQ-2", "dependency", {"depends_on_roles": ["A"]}),
    ]
    first = QuantitySolver().solve(rules)
    second = QuantitySolver().solve(rules)
    assert first == second
    assert all("角色依赖存在循环" in item.conflicts for item in first)


def test_mutual_exclusion_reports_active_conflict() -> None:
    rules = [
        _rule("R-A", "主配置", "EQ-1", "fixed", {"quantity": 1}),
        _rule(
            "R-MUTEX",
            "主配置",
            "EQ-1",
            "mutual_exclusion",
            {"excluded_roles": ["备用配置"]},
        ),
        _rule("R-B", "备用配置", "EQ-2", "fixed", {"quantity": 1}),
    ]
    result = QuantitySolver().solve(rules)
    primary = next(item for item in result if item.role == "主配置")
    assert "互斥配置冲突:备用配置" in primary.conflicts
    assert primary.need_human_confirm is True


def test_manual_only_never_guesses_quantity() -> None:
    rule = _rule(
        "R-MANUAL",
        "特殊设备",
        "EQ-1",
        "manual_only",
        {"reason": "任务负责人指定"},
    )
    missing = QuantitySolver().solve([rule])[0]
    specified = QuantitySolver().solve(
        [rule], manual_quantities={"特殊设备": 2}
    )[0]
    assert missing.quantity is None
    assert missing.need_human_confirm is True
    assert specified.quantity == 2
    assert specified.quantity_source == "manual_only"


def test_no_rule_returns_null_quantity() -> None:
    result = QuantitySolver().solve(
        [], requested_allocations=[("未知角色", "EQ-1")]
    )[0]
    assert result.quantity is None
    assert result.quantity_source == "unknown"
    assert result.need_human_confirm is True
    assert "没有数量规则" in result.warnings[0]


def test_numeric_and_parameter_validation_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        _rule("R-BAD", "角色", "EQ-1", "fixed", {"quantity": -1})
    with pytest.raises(ValidationError):
        _rule(
            "R-BAD-RANGE",
            "角色",
            "EQ-1",
            "min_max",
            {"minimum": 5, "maximum": 2},
        )
    capacity = _rule(
        "R-CAP", "角色", "EQ-1", "capacity", {"demand_variable": "d", "capacity_per_unit": 2}
    )
    with pytest.raises(ValueError, match="non-negative"):
        QuantitySolver().solve([capacity], input_values={"d": -1})
