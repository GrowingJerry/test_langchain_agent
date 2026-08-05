from application.services.test_type_recommendation import (
    apply_manual_case_type,
    recommendation_for_requirements,
    sync_case_type_state,
)
from domain.rules.test_types import LABELS

FUNCTIONAL = LABELS[0]
PERFORMANCE = LABELS[1]
INTERFACE = LABELS[2]
SECURITY = LABELS[4]


def req(rid: str, main: str = "", confidence: float = 0.0, reasons=None, alts=None):
    return {
        "requirement_id": rid,
        "recommended_test_type": main,
        "alternative_test_types": alts or [],
        "test_type_confidence": confidence,
        "test_type_reasons": reasons or [],
    }


def test_single_performance_requirement_defaults_to_performance() -> None:
    state = {}
    model = sync_case_type_state(
        state,
        [req("REQ-PERF-001", PERFORMANCE, 0.92)],
        current_default=FUNCTIONAL,
    )
    assert model["selected_case_type"] == PERFORMANCE
    assert model["recommended_case_type"] == PERFORMANCE


def test_single_security_requirement_defaults_to_security() -> None:
    model = recommendation_for_requirements(
        [req("REQ-SEC-001", SECURITY, 0.88)],
        current_default=FUNCTIONAL,
    )
    assert model["recommended_case_type"] == SECURITY


def test_explicit_type_beats_semantic_confidence() -> None:
    model = recommendation_for_requirements(
        [
            req("REQ-1", FUNCTIONAL, 0.61, ["原文明确填写测试类型：功能测试"]),
            req("REQ-2", PERFORMANCE, 0.95),
        ],
        current_default=INTERFACE,
    )
    assert model["recommended_case_type"] == FUNCTIONAL


def test_manual_override_survives_rerun_until_requirement_set_changes() -> None:
    state = {}
    sync_case_type_state(state, [req("REQ-1", PERFORMANCE, 0.9)], current_default=FUNCTIONAL)
    apply_manual_case_type(state, FUNCTIONAL)
    model = sync_case_type_state(
        state,
        [req("REQ-1", PERFORMANCE, 0.9)],
        current_default=FUNCTIONAL,
    )
    assert model["selected_case_type"] == FUNCTIONAL
    assert model["case_type_manually_overridden"] is True
    changed = sync_case_type_state(
        state,
        [req("REQ-2", SECURITY, 0.9)],
        current_default=FUNCTIONAL,
    )
    assert changed["selected_case_type"] == SECURITY
    assert changed["case_type_manually_overridden"] is False


def test_multiple_same_type_and_mixed_distribution() -> None:
    same = recommendation_for_requirements(
        [req("A", INTERFACE, 0.8), req("B", INTERFACE, 0.7)],
        current_default=FUNCTIONAL,
    )
    assert same["recommended_case_type"] == INTERFACE
    assert same["type_distribution"] == {INTERFACE: 2}
    mixed = recommendation_for_requirements(
        [
            req("A", FUNCTIONAL, 0.8),
            req("B", INTERFACE, 0.9),
            req("C", PERFORMANCE, 0.7),
        ],
        current_default=FUNCTIONAL,
    )
    assert mixed["type_distribution"] == {FUNCTIONAL: 1, INTERFACE: 1, PERFORMANCE: 1}
    assert mixed["needs_human_confirm"] is True


def test_unknown_recommendation_safely_falls_back() -> None:
    model = recommendation_for_requirements(
        [req("REQ-X", "unknown-test-type", 0.9)],
        current_default=SECURITY,
    )
    assert model["recommended_case_type"] == SECURITY
    assert model["needs_human_confirm"] is True
