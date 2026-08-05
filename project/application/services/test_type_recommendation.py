"""Recommendation state helpers for requirement-driven generation UI."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from domain.rules.test_types import LABELS, normalize_test_type


DEFAULT_STATE = {
    "selected_requirement_ids": [],
    "recommended_case_type": "",
    "recommended_alternatives": [],
    "recommendation_reasons": [],
    "recommendation_confidence": 0.0,
    "selected_case_type": "",
    "case_type_manually_overridden": False,
    "selected_case_count": 1,
    "type_distribution": {},
    "needs_human_confirm": False,
}


def _ids(requirements: List[Dict[str, Any]]) -> List[str]:
    return sorted(str(row.get("requirement_id") or "") for row in requirements)


def _safe_type(value: object, fallback: str = "") -> str:
    normalized = normalize_test_type(value)
    if normalized in LABELS:
        return normalized
    text = str(value or "").strip()
    if text in LABELS:
        return text
    if fallback in LABELS:
        return fallback
    return ""


def recommendation_for_requirements(
    requirements: List[Dict[str, Any]],
    current_default: str = "",
) -> Dict[str, Any]:
    """Summarize recommendation for one or more selected requirements."""
    fallback = _safe_type(current_default)
    if not requirements:
        return {
            "recommended_case_type": fallback,
            "recommended_alternatives": [],
            "recommendation_reasons": ["未选择需求，请人工选择测试类型"],
            "recommendation_confidence": 0.0,
            "type_distribution": {},
            "needs_human_confirm": True,
        }
    scored: List[tuple[str, float, int]] = []
    distribution: Counter[str] = Counter()
    reasons: List[str] = []
    alternatives: List[str] = []
    needs_confirm = False
    for row in requirements:
        main = _safe_type(row.get("recommended_test_type"), "")
        alts = [_safe_type(item, "") for item in row.get("alternative_test_types") or []]
        alts = [item for item in alts if item]
        confidence = float(row.get("test_type_confidence") or 0)
        explicit = any("原文明确" in str(reason) for reason in row.get("test_type_reasons") or [])
        chosen = main or (alts[0] if alts else "")
        if chosen:
            distribution[chosen] += 1
            scored.append((chosen, confidence, 1 if explicit else 0))
        else:
            needs_confirm = True
        alternatives.extend(alts)
        reasons.extend(str(reason) for reason in row.get("test_type_reasons") or [])
        if row.get("need_human_confirm"):
            needs_confirm = True
    if not scored:
        return {
            "recommended_case_type": fallback,
            "recommended_alternatives": [],
            "recommendation_reasons": ["未能从需求中确定测试类型，请人工选择"],
            "recommendation_confidence": 0.0,
            "type_distribution": {},
            "needs_human_confirm": True,
        }
    totals: Dict[str, Dict[str, float]] = {}
    for label, confidence, explicit in scored:
        bucket = totals.setdefault(label, {"count": 0, "confidence": 0.0, "explicit": 0})
        bucket["count"] += 1
        bucket["confidence"] += confidence
        bucket["explicit"] += explicit
    recommended = sorted(
        totals,
        key=lambda label: (
            -totals[label]["explicit"],
            -(totals[label]["confidence"] / max(totals[label]["count"], 1)),
            -totals[label]["count"],
            LABELS.index(label) if label in LABELS else 999,
        ),
    )[0]
    confidence = totals[recommended]["confidence"] / max(totals[recommended]["count"], 1)
    if len(distribution) > 1:
        needs_confirm = True
        reasons.insert(0, "所选需求推荐类型不一致，一次生成只能采用一个最终测试类型")
    return {
        "recommended_case_type": recommended,
        "recommended_alternatives": [item for item in dict.fromkeys(alternatives) if item != recommended],
        "recommendation_reasons": list(dict.fromkeys(reasons))[:8],
        "recommendation_confidence": round(confidence, 4),
        "type_distribution": dict(distribution),
        "needs_human_confirm": needs_confirm or confidence < 0.7,
    }


def sync_case_type_state(
    state: Dict[str, Any],
    requirements: List[Dict[str, Any]],
    *,
    current_default: str = "",
) -> Dict[str, Any]:
    """Recompute recommendation only when the selected requirement set changes."""
    state.setdefault("case_type_recommendation", dict(DEFAULT_STATE))
    model = state["case_type_recommendation"]
    selected_ids = _ids(requirements)
    if selected_ids != list(model.get("selected_requirement_ids") or []):
        recommendation = recommendation_for_requirements(requirements, current_default)
        model.update(DEFAULT_STATE)
        model.update(recommendation)
        model["selected_requirement_ids"] = selected_ids
        model["selected_case_type"] = recommendation["recommended_case_type"] or _safe_type(current_default)
        model["case_type_manually_overridden"] = False
    elif model.get("selected_case_type") not in LABELS:
        model["selected_case_type"] = _safe_type(model.get("recommended_case_type") or current_default)
    return model


def apply_manual_case_type(state: Dict[str, Any], selected_case_type: str) -> Dict[str, Any]:
    model = state.setdefault("case_type_recommendation", dict(DEFAULT_STATE))
    selected = _safe_type(selected_case_type, model.get("recommended_case_type", ""))
    model["selected_case_type"] = selected
    model["case_type_manually_overridden"] = selected != model.get("recommended_case_type")
    return model
