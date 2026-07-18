# -*- coding: utf-8 -*-
"""需求追踪矩阵与六性覆盖矩阵生成。"""

from collections import defaultdict
from typing import Dict, List

from models.schemas import RequirementItem, TestCaseItem


def build_requirement_trace_matrix(
    requirements: List[RequirementItem],
    test_cases: List[TestCaseItem],
) -> List[Dict[str, str]]:
    """需求-用例追踪矩阵行列表。"""
    rows: List[Dict[str, str]] = []
    for tc in test_cases:
        req_text = tc.requirement_text
        for r in requirements:
            if r.requirement_id == tc.requirement_id:
                req_text = r.requirement_text
                break
        six = "、".join(tc.six_quality_attribute) if tc.six_quality_attribute else ""
        rows.append(
            {
                "requirement_id": tc.requirement_id,
                "requirement_text": req_text,
                "case_id": tc.case_id,
                "test_type": tc.test_type,
                "six_quality_attribute": six,
                "test_method": tc.test_method,
                "coverage_status": "已分配用例",
            }
        )
    return rows


def build_six_quality_coverage_matrix(
    requirements: List[RequirementItem],
    test_cases: List[TestCaseItem],
) -> List[Dict[str, str]]:
    """六性覆盖统计。"""
    req_by_six: Dict[str, int] = defaultdict(int)
    for r in requirements:
        cats = r.six_quality_attribute or ["未分类"]
        for c in cats:
            req_by_six[c] += 1

    case_by_six: Dict[str, int] = defaultdict(int)
    for tc in test_cases:
        cats = tc.six_quality_attribute or ["未分类"]
        for c in cats:
            case_by_six[c] += 1

    all_cats = sorted(set(list(req_by_six.keys()) + list(case_by_six.keys())))
    out: List[Dict[str, str]] = []
    for cat in all_cats:
        rc = req_by_six.get(cat, 0)
        cc = case_by_six.get(cat, 0)
        rate = "0%"
        if rc > 0:
            rate = "{:.0%}".format(min(1.0, cc / max(rc, 1)))
        out.append(
            {
                "six_quality_attribute": cat,
                "requirement_count": str(rc),
                "case_count": str(cc),
                "coverage_rate": rate,
            }
        )
    return out
