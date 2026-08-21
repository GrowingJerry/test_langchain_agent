"""Generated-case review, editing, and traceability page."""

import json
import re
from typing import Dict, List
import streamlit as st

from ui.traceability_page import render_case_optimization_panel


def _editable_lines(value) -> str:
    """Render mixed LLM output (strings/dicts/lists) safely in a text area."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, str):
                lines.append(item)
            elif isinstance(item, (dict, list)):
                lines.append(json.dumps(item, ensure_ascii=False))
            elif item is not None:
                lines.append(str(item))
        return "\n".join(lines)
    return str(value)


def _rule_issues(case: Dict) -> List[str]:
    issues = []
    for fields, label in [
        (("test_purpose",), "测试目的"),
        (("test_steps",), "测试步骤"),
        (("expected_result", "expected_results"), "预期结果"),
        (("pass_criteria",), "判定准则"),
    ]:
        if not any(case.get(field) for field in fields):
            issues.append(f"缺少{label}")
    text = json.dumps(case, ensure_ascii=False)
    if any(
        k in text for k in ("性能", "响应", "并发", "吞吐", "时延")
    ) and not re.search(r"\d", text):
        issues.append("缺少判定阈值，需人工确认")
    if case.get("need_human_confirmation") or case.get("need_human_confirm"):
        issues.append("生成器标记为需人工确认")
    return issues


def _as_id_list(value) -> List[str]:
    """Normalize evidence source fields into a list of ids."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[,，;；\s]+", value) if x.strip()]
    return [str(value).strip()]


def _visual_evidence_summary_rows(
    cases_by_id: Dict, visual_evidence: List[Dict]
) -> List[Dict]:
    """Join generated cases with project-scoped visual evidence records."""
    evidence_by_id = {str(row.get("evidence_id")): row for row in visual_evidence}
    rows = []
    for case_id, case_row in cases_by_id.items():
        case_json = case_row.get("case_json") or {}
        for evidence_id in _as_id_list(case_json.get("evidence_sources")):
            evidence = evidence_by_id.get(evidence_id)
            if not evidence:
                rows.append(
                    {
                        "case_id": case_id,
                        "evidence_id": evidence_id,
                        "asset_id": "",
                        "image_type": "",
                        "visible_text": "当前项目未找到该视觉证据",
                        "possible_test_points": "",
                        "need_human_confirm": True,
                    }
                )
                continue
            payload = evidence.get("evidence") or {}
            visible_text = (
                payload.get("visible_text") or evidence.get("visible_text_items") or []
            )
            test_points = payload.get("possible_test_points") or []
            rows.append(
                {
                    "case_id": case_id,
                    "evidence_id": evidence_id,
                    "asset_id": evidence.get("asset_id"),
                    "image_type": payload.get("image_type")
                    or evidence.get("evidence_type"),
                    "visible_text": "；".join(str(x) for x in visible_text[:5]),
                    "possible_test_points": "；".join(str(x) for x in test_points[:5]),
                    "need_human_confirm": bool(
                        payload.get(
                            "need_human_confirm", evidence.get("need_human_confirm")
                        )
                    ),
                }
            )
    return rows


def render_review_trace_page(service, project_id, config) -> None:
    st.header("用例审查")
    if isinstance(config, dict):
        use_ollama_review = bool(config.get("use_ollama_review", False))
        use_ollama = bool(config.get("use_ollama", False))
        model_name = str(config.get("model") or "")
    else:
        use_ollama_review = bool(config)
        use_ollama = True
        model_name = ""
    if not project_id:
        st.info("请先选择项目。")
        return
    saved = service.list_generated_cases(project_id)
    if not saved:
        st.info("当前项目尚未生成测试用例。")
        return
    section = st.radio(
        "当前任务",
        ["审查与人工修改", "单条对话优化", "覆盖与来源"],
        horizontal=True,
        key=f"review_section_{project_id}",
    )
    if section == "单条对话优化":
        render_case_optimization_panel(service, project_id)
        return
    if section == "覆盖与来源":
        trace_rows = service.traceability_rows(project_id)
        coverage = trace_rows.get("atomic_coverage_matrix") or []
        if coverage:
            st.subheader("原子需求覆盖矩阵")
            st.dataframe(coverage, hide_index=True, use_container_width=True)
        else:
            st.info("尚无原子需求覆盖数据；请先在“资料与需求”确认需求结构。")
        rows = service.export_rows(project_id)
        st.subheader("需求—用例追踪矩阵")
        st.dataframe(
            rows["requirement_case_matrix"], hide_index=True, use_container_width=True
        )
        with st.expander("来源文档与片段"):
            st.dataframe(
                rows["trace_sources"], hide_index=True, use_container_width=True
            )
        return
    cases_by_id = {row["case_id"]: row for row in saved}
    if st.button("执行规则审查", key=f"rule_review_{project_id}"):
        service.review_project(project_id, include_llm=False)
        st.success("规则审查完成。")
    if isinstance(config, dict):
        use_ollama_review = st.checkbox(
            "启用 Ollama 审查",
            value=use_ollama_review,
            disabled=not use_ollama,
            key=f"enable_ollama_review_{project_id}",
        )
        config["use_ollama_review"] = bool(use_ollama_review)
    if not use_ollama:
        st.caption("Ollama 审查不可用：系统设置中未启用 Ollama。")
    elif not use_ollama_review:
        st.caption("Ollama 审查未启用：勾选上方“启用 Ollama 审查”后即可执行。")
    elif model_name:
        st.caption(f"Ollama 审查已启用，当前模型：{model_name}")
    if st.button(
        "执行 Ollama 审查",
        disabled=(not use_ollama or not use_ollama_review),
        key=f"ollama_review_{project_id}",
    ):
        try:
            service.review_project(project_id, include_llm=True)
            st.success("Ollama 审查完成。")
        except Exception as exc:
            st.error(f"结构化 LLM 审查失败，原用例未修改：{type(exc).__name__}: {exc}")
    reviews = service.list_review_results(project_id)
    if reviews:
        st.dataframe(reviews, hide_index=True, use_container_width=True)
    scores = service.list_quality_scores(project_id)
    st.subheader("场景化用例质量评分")
    if scores:
        score_rows = [
            {
                "用例编号": row.get("case_id"),
                "总分": row.get("score"),
                **(row.get("dimensions") or {}),
                "问题": "；".join(row.get("issues") or []),
                "建议": "；".join(row.get("suggestions") or []),
            }
            for row in scores
        ]
        st.dataframe(score_rows, hide_index=True, use_container_width=True)
    else:
        st.caption(
            "普通兼容用例可能没有高级 Agent 质量评分；场景化用例生成后会自动评分。"
        )
    st.subheader("人工修改")
    case_id = st.selectbox(
        "选择用例", list(cases_by_id), key=f"review_case_{project_id}"
    )
    case = dict(cases_by_id[case_id].get("case_json") or {})
    if case.get("quality_issues"):
        st.warning("待处理质量问题")
        st.json(case.get("quality_issues"))
    actions = st.columns(2)
    if actions[0].button("接受草稿", disabled=case.get("review_status") != "draft_needs_review", key=f"accept_draft_{project_id}_{case_id}"):
        service.set_case_review_status(project_id, case_id, "accepted")
        st.success("草稿已接受，覆盖关系已更新为确认覆盖。")
        st.rerun()
    if actions[1].button("停用用例", disabled=case.get("review_status") == "inactive", key=f"disable_case_{project_id}_{case_id}"):
        service.set_case_review_status(project_id, case_id, "inactive")
        st.success("用例已停用，历史数据仍保留。")
        st.rerun()
    with st.form(f"edit_case_{project_id}_{case_id}"):
        case["case_name"] = st.text_input("用例名称", case.get("case_name", ""))
        case["test_purpose"] = st.text_area("测试目的", case.get("test_purpose", ""))
        case["test_steps"] = [
            x
            for x in st.text_area(
                "测试步骤（每行一步）", _editable_lines(case.get("test_steps"))
            ).splitlines()
            if x
        ]
        expected = case.get("expected_result") or case.get("expected_results") or []
        case["expected_result"] = [
            x
            for x in st.text_area(
                "预期结果（每行一条）", _editable_lines(expected)
            ).splitlines()
            if x
        ]
        case["pass_criteria"] = st.text_area("判定准则", case.get("pass_criteria", ""))
        case["need_human_confirm"] = st.checkbox(
            "需人工确认",
            bool(case.get("need_human_confirm") or case.get("need_human_confirmation")),
        )
        if st.form_submit_button("保存修改 / 确认结果"):
            case["case_id"] = case_id
            service.confirm_case_update(
                project_id,
                case_id,
                case,
                cases_by_id[case_id].get("generation_run_id", ""),
            )
            st.success("修改已保存，原来源追溯关系保持不变。")
    rows = service.export_rows(project_id)
    visual_evidence = service.list_visual_evidence_by_project(project_id)
    visual_rows = _visual_evidence_summary_rows(cases_by_id, visual_evidence)
    if visual_rows:
        st.subheader("视觉证据来源")
        st.dataframe(visual_rows, hide_index=True, use_container_width=True)
        if any(row.get("need_human_confirm") for row in visual_rows):
            st.warning("存在需要人工确认的视觉证据，不建议作为唯一测试依据。")
    if visual_evidence:
        with st.expander("当前项目视觉证据详情"):
            st.json(visual_evidence)
    st.info("确认或修改完成后，可切换到“覆盖与来源”检查追踪，再进入导出中心。")
