# -*- coding: utf-8 -*-
"""Word 占位符行构建，供 Excel document_placeholders 使用。"""

from typing import Dict, List

from domain.schemas.generation import PlaceholderRow, TestCaseItem


# 与 Word 模板联动的常用占位符名（顺序稳定）
STANDARD_PLACEHOLDER_NAMES = [
    "requirement_id",
    "requirement_text",
    "case_id",
    "test_object",
    "scenario_name",
    "six_quality_attribute",
    "test_type",
    "test_purpose",
    "test_basis",
    "test_method",
    "test_condition",
    "test_environment",
    "test_steps",
    "expected_result",
    "pass_criteria",
    "record_items",
    "test_result_template",
    "review_score",
    "review_status",
]


def _numbered_lines(items: List[str]) -> str:
    """列表转为带编号的多行文本。"""
    lines = []
    for i, s in enumerate(items or [], start=1):
        lines.append(f"{i}. {s}")
    return "\n".join(lines)


def _safe_str(val) -> str:
    """转字符串，避免 None。"""
    if val is None:
        return ""
    return str(val)


def build_placeholder_rows(
    test_cases: List[TestCaseItem],
    doc_id_prefix: str = "DOC",
) -> List[Dict[str, str]]:
    """
    把每条测试用例展开为多行占位符键值表。
    返回字典列表，便于写入 DataFrame。
    """
    rows: List[Dict[str, str]] = []
    for idx, tc in enumerate(test_cases, start=1):
        doc_id = f"{doc_id_prefix}-{idx:03d}"
        six_joined = "、".join(tc.six_quality_attribute or [])
        basis_lines = tc.test_basis or ""
        steps_merged = _numbered_lines(list(tc.test_steps or []))
        records_merged = _numbered_lines(list(tc.record_items or []))

        value_map: Dict[str, str] = {
            "requirement_id": _safe_str(tc.requirement_id),
            "requirement_text": _safe_str(tc.requirement_text),
            "case_id": _safe_str(tc.case_id),
            "test_object": _safe_str(tc.test_object),
            "scenario_name": _safe_str(tc.scenario_name),
            "six_quality_attribute": six_joined,
            "test_type": _safe_str(tc.test_type),
            "test_purpose": _safe_str(tc.test_purpose),
            "test_basis": basis_lines,
            "test_method": _safe_str(tc.test_method),
            "test_condition": _safe_str(tc.test_condition),
            "test_environment": _safe_str(tc.test_environment),
            "test_steps": steps_merged,
            "expected_result": _safe_str(tc.expected_result),
            "pass_criteria": _safe_str(tc.pass_criteria),
            "record_items": records_merged,
            "test_result_template": _safe_str(tc.test_result_template),
            "review_score": _safe_str(tc.review_score),
            "review_status": _safe_str(tc.review_status),
        }

        for name in STANDARD_PLACEHOLDER_NAMES:
            rows.append(
                {
                    "doc_id": doc_id,
                    "case_id": tc.case_id,
                    "requirement_id": tc.requirement_id,
                    "placeholder_name": name,
                    "placeholder_value": value_map.get(name, ""),
                    "value_type": "text",
                    "source_sheet": "generated_test_cases",
                }
            )
    return rows


def placeholder_models(test_cases: List[TestCaseItem]) -> List[PlaceholderRow]:
    """转为 PlaceholderRow 模型列表。"""
    return [PlaceholderRow(**r) for r in build_placeholder_rows(test_cases)]
