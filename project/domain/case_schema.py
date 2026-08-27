"""Canonical generated-case normalization, merging, and validation."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


ALIASES = {
    "steps": "test_steps",
    "测试步骤": "test_steps",
    "expected": "expected_result",
    "expected_results": "expected_result",
    "预期结果": "expected_result",
}
PROTECTED_FIELDS = {
    "case_id", "project_id", "requirement_id", "requirement_ids", "indicator_ids",
    "source_chunk_ids", "source_documents", "requirement_hierarchy_path",
    "provenance", "generation_run_id",
}
EDITABLE_FIELDS = {
    "case_name", "case_type", "test_purpose", "prerequisites", "test_steps",
    "expected_result", "pass_criteria", "need_human_confirm", "expected_source",
    "review_status", "quality_issues",
}
MAX_CASE_STEPS = 100


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != []


def normalize_case(case: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a deep-copied canonical case while accepting legacy aliases."""
    source = deepcopy(dict(case or {}))
    result = {key: value for key, value in source.items() if key not in ALIASES}
    for legacy, canonical in ALIASES.items():
        if not _present(result.get(canonical)) and _present(source.get(legacy)):
            result[canonical] = deepcopy(source[legacy])
    return result


def feedback_scope(feedback: str) -> str:
    """Classify explicit narrow edits; unknown feedback remains safely mergeable."""
    text = (feedback or "").replace(" ", "")
    step_change = any(word in text for word in ("增加步骤", "新增步骤", "补充步骤", "删除步骤", "合并步骤", "拆分步骤"))
    if step_change:
        return "steps"
    if any(word in text for word in ("只修改预期", "仅修改预期", "测试步骤不变")):
        return "expected_only"
    if any(word in text for word in ("只修改名称", "仅修改名称", "其他内容不变")) and "名称" in text:
        return "name_only"
    return "general"


def merge_case(original: Mapping[str, Any], revision: Mapping[str, Any], feedback: str = "") -> dict[str, Any]:
    """Merge a possibly sparse model response onto the complete formal case."""
    base = normalize_case(original)
    proposed = normalize_case(revision)
    scope = feedback_scope(feedback)
    allowed = {"expected_result"} if scope == "expected_only" else {"case_name"} if scope == "name_only" else EDITABLE_FIELDS
    for field in allowed:
        if field in proposed and _present(proposed[field]):
            base[field] = deepcopy(proposed[field])
    for field in PROTECTED_FIELDS:
        if field in normalize_case(original):
            base[field] = deepcopy(normalize_case(original)[field])
    return base


def validate_case(case: Mapping[str, Any], *, case_id: str | None = None) -> dict[str, Any]:
    """Validate the canonical invariants required by persistence and export."""
    value = normalize_case(case)
    if case_id is not None and value.get("case_id") != case_id:
        raise ValueError("用例编号与目标用例不一致")
    steps = value.get("test_steps")
    expected = value.get("expected_result")
    if not isinstance(steps, list) or not isinstance(expected, list):
        raise ValueError("测试步骤与预期结果必须是列表")
    if not steps or len(steps) > MAX_CASE_STEPS or len(steps) != len(expected):
        raise ValueError("测试步骤与预期结果必须非空且一一对应")
    if any(not isinstance(item, str) or not item.strip() for item in steps + expected):
        raise ValueError("测试步骤与预期结果每项必须是非空字符串")
    return value
