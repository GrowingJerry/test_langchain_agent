# -*- coding: utf-8 -*-
"""标准化 Excel 工作簿导出。"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from config.settings import OUTPUT_EXCEL_DIR
from core.matrix_generator import (
    build_requirement_trace_matrix,
    build_six_quality_coverage_matrix,
)
from core.placeholder_builder import build_placeholder_rows
from models.schemas import RequirementItem, ReviewResult, ScenarioItem, TestCaseItem


def _now_str() -> str:
    """当前时间字符串。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def requirements_rows(requirements: List[RequirementItem]) -> List[Dict[str, Any]]:
    """requirements Sheet 数据。"""
    rows = []
    for r in requirements:
        rows.append(
            {
                "requirement_id": r.requirement_id,
                "requirement_text": r.requirement_text,
                "test_object": r.test_object,
                "source_type": r.source_type,
                "six_quality_attribute": "、".join(r.six_quality_attribute or []),
                "priority": r.priority,
                "created_at": _now_str(),
            }
        )
    return rows


def scenarios_rows(
    requirements: List[RequirementItem],
    scenarios: Optional[List[ScenarioItem]] = None,
) -> List[Dict[str, Any]]:
    """scenarios Sheet：独立场景列表优先，否则从需求抽取。"""
    rows: List[Dict[str, Any]] = []
    if scenarios:
        for s in scenarios:
            rows.append(
                {
                    "scenario_id": s.scenario_id,
                    "requirement_id": s.requirement_id,
                    "scenario_name": s.scenario_name,
                    "scenario_environment": s.scenario_environment,
                    "initial_condition": s.initial_condition,
                    "trigger_event": s.trigger_event,
                    "expected_behavior": s.expected_behavior,
                    "evaluation_metrics": s.evaluation_metrics,
                }
            )
        return rows

    for r in requirements:
        if not any(
            [
                r.scenario_name,
                r.scenario_environment,
                r.initial_condition,
                r.trigger_event,
            ]
        ):
            continue
        sid = f"SCE-{r.requirement_id}"
        rows.append(
            {
                "scenario_id": sid,
                "requirement_id": r.requirement_id,
                "scenario_name": r.scenario_name,
                "scenario_environment": r.scenario_environment,
                "initial_condition": r.initial_condition,
                "trigger_event": r.trigger_event,
                "expected_behavior": r.expected_behavior,
                "evaluation_metrics": r.evaluation_metrics,
            }
        )
    return rows


def generated_test_cases_rows(cases: List[TestCaseItem]) -> List[Dict[str, Any]]:
    """generated_test_cases Sheet（步骤与记录项拆到独表）。"""
    out = []
    for tc in cases:
        out.append(
            {
                "case_id": tc.case_id,
                "requirement_id": tc.requirement_id,
                "requirement_text": tc.requirement_text,
                "test_object": tc.test_object,
                "scenario_name": tc.scenario_name,
                "six_quality_attribute": "、".join(tc.six_quality_attribute or []),
                "test_type": tc.test_type,
                "test_purpose": tc.test_purpose,
                "test_basis": tc.test_basis,
                "test_method": tc.test_method,
                "test_condition": tc.test_condition,
                "test_environment": tc.test_environment,
                "expected_result": tc.expected_result,
                "pass_criteria": tc.pass_criteria,
                "test_result_template": tc.test_result_template,
                "related_library_cases": "、".join(tc.related_library_cases or []),
                "review_score": tc.review_score,
                "review_status": tc.review_status,
                "suggestions": tc.suggestions,
            }
        )
    return out


def test_steps_rows(cases: List[TestCaseItem]) -> List[Dict[str, Any]]:
    """test_steps Sheet。"""
    rows = []
    for tc in cases:
        for i, step in enumerate(tc.test_steps or [], start=1):
            rows.append({"case_id": tc.case_id, "step_no": i, "step_text": step})
    return rows


def record_items_rows(cases: List[TestCaseItem]) -> List[Dict[str, Any]]:
    """record_items Sheet。"""
    rows = []
    for tc in cases:
        for i, item in enumerate(tc.record_items or [], start=1):
            rows.append({"case_id": tc.case_id, "item_no": i, "record_item": item})
    return rows


def review_results_rows(reviews: List[ReviewResult]) -> List[Dict[str, Any]]:
    """review_results Sheet。"""
    return [
        {
            "case_id": r.case_id,
            "review_score": r.review_score,
            "review_status": r.review_status,
            "review_issues": "\n".join(r.review_issues or []),
            "review_suggestions": "\n".join(r.review_suggestions or []),
        }
        for r in reviews
    ]


def doc_generation_tasks_rows(cases: List[TestCaseItem]) -> List[Dict[str, Any]]:
    """doc_generation_tasks：为每条用例生成多类模板任务。"""
    rows = []
    types = ["测试用例", "测试说明", "测试记录", "测试报告"]
    doc_counter = 1
    for tc in cases:
        doc_base = f"{tc.requirement_id}_{tc.case_id}"
        for t in types:
            ext = ".docx"
            out_name = f"{doc_base}_{t}{ext}"
            rows.append(
                {
                    "doc_id": f"DOC-{doc_counter:03d}",
                    "case_id": tc.case_id,
                    "requirement_id": tc.requirement_id,
                    "template_type": t,
                    "template_file": "",
                    "output_file": out_name,
                    "status": "pending",
                }
            )
            doc_counter += 1
    return rows


def infer_retrieved_library_rows(
    test_cases: List[TestCaseItem],
    library: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """从用例的 related_library_cases 反推检索结果表；若提供 library 则补全名称。"""
    rows: List[Dict[str, Any]] = []
    for tc in test_cases:
        for lib_id in tc.related_library_cases or []:
            name = ""
            src = ""
            if library is not None:
                item = library.get_case_by_id(lib_id)
                if item:
                    name = item.case_name
                    src = item.source_file or ""
            rows.append(
                {
                    "requirement_id": tc.requirement_id,
                    "case_id": tc.case_id,
                    "library_case_id": lib_id,
                    "library_case_name": name or lib_id,
                    "similarity_score": 0.0,
                    "source_file": src,
                    "matched_reason": "生成阶段关联的历史用例",
                }
            )
    return rows


def export_full_workbook(
    requirements: List[RequirementItem],
    test_cases: List[TestCaseItem],
    reviews: List[ReviewResult],
    output_path: Optional[Path] = None,
    scenarios: Optional[List[ScenarioItem]] = None,
    retrieved_library_cases: Optional[List[Dict[str, Any]]] = None,
    library: Optional[Any] = None,
) -> Path:
    """
    导出完整 test_document_data.xlsx。
    retrieved_library_cases 由调用方在生成时收集。
    """
    OUTPUT_EXCEL_DIR.mkdir(parents=True, exist_ok=True)
    path = output_path or (OUTPUT_EXCEL_DIR / "test_document_data.xlsx")

    df_req = pd.DataFrame(requirements_rows(requirements))
    df_sce = pd.DataFrame(scenarios_rows(requirements, scenarios))
    df_cases = pd.DataFrame(generated_test_cases_rows(test_cases))
    df_steps = pd.DataFrame(test_steps_rows(test_cases))
    df_records = pd.DataFrame(record_items_rows(test_cases))
    if retrieved_library_cases is None:
        retrieved_library_cases = infer_retrieved_library_rows(
            test_cases, library=library
        )
    df_ret = pd.DataFrame(retrieved_library_cases)
    df_rev = pd.DataFrame(review_results_rows(reviews))
    df_trace = pd.DataFrame(build_requirement_trace_matrix(requirements, test_cases))
    df_cov = pd.DataFrame(build_six_quality_coverage_matrix(requirements, test_cases))
    df_ph = pd.DataFrame(build_placeholder_rows(test_cases))
    df_tasks = pd.DataFrame(doc_generation_tasks_rows(test_cases))

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df_req.to_excel(writer, sheet_name="requirements", index=False)
        df_sce.to_excel(writer, sheet_name="scenarios", index=False)
        df_cases.to_excel(writer, sheet_name="generated_test_cases", index=False)
        df_steps.to_excel(writer, sheet_name="test_steps", index=False)
        df_records.to_excel(writer, sheet_name="record_items", index=False)
        df_ret.to_excel(writer, sheet_name="retrieved_library_cases", index=False)
        df_rev.to_excel(writer, sheet_name="review_results", index=False)
        df_trace.to_excel(writer, sheet_name="requirement_trace_matrix", index=False)
        df_cov.to_excel(writer, sheet_name="six_quality_coverage_matrix", index=False)
        df_ph.to_excel(writer, sheet_name="document_placeholders", index=False)
        df_tasks.to_excel(writer, sheet_name="doc_generation_tasks", index=False)

    return path


def export_test_cases_only(
    test_cases: List[TestCaseItem], path: Optional[Path] = None
) -> Path:
    """仅导出用例主表 + 步骤 + 记录项。"""
    OUTPUT_EXCEL_DIR.mkdir(parents=True, exist_ok=True)
    p = path or (OUTPUT_EXCEL_DIR / "test_cases.xlsx")
    with pd.ExcelWriter(p, engine="openpyxl") as writer:
        pd.DataFrame(generated_test_cases_rows(test_cases)).to_excel(
            writer, sheet_name="generated_test_cases", index=False
        )
        pd.DataFrame(test_steps_rows(test_cases)).to_excel(
            writer, sheet_name="test_steps", index=False
        )
        pd.DataFrame(record_items_rows(test_cases)).to_excel(
            writer, sheet_name="record_items", index=False
        )
    return p


def export_placeholders_only(
    test_cases: List[TestCaseItem], path: Optional[Path] = None
) -> Path:
    """仅导出 document_placeholders。"""
    OUTPUT_EXCEL_DIR.mkdir(parents=True, exist_ok=True)
    p = path or (OUTPUT_EXCEL_DIR / "document_placeholders.xlsx")
    pd.DataFrame(build_placeholder_rows(test_cases)).to_excel(p, index=False)
    return p


def export_trace_matrix_only(
    requirements: List[RequirementItem],
    test_cases: List[TestCaseItem],
    path: Optional[Path] = None,
) -> Path:
    """仅导出需求追踪矩阵。"""
    OUTPUT_EXCEL_DIR.mkdir(parents=True, exist_ok=True)
    p = path or (OUTPUT_EXCEL_DIR / "requirement_trace_matrix.xlsx")
    pd.DataFrame(build_requirement_trace_matrix(requirements, test_cases)).to_excel(
        p, index=False
    )
    return p


def export_coverage_matrix_only(
    requirements: List[RequirementItem],
    test_cases: List[TestCaseItem],
    path: Optional[Path] = None,
) -> Path:
    """仅导出六性覆盖矩阵。"""
    OUTPUT_EXCEL_DIR.mkdir(parents=True, exist_ok=True)
    p = path or (OUTPUT_EXCEL_DIR / "six_quality_coverage_matrix.xlsx")
    pd.DataFrame(build_six_quality_coverage_matrix(requirements, test_cases)).to_excel(
        p, index=False
    )
    return p
