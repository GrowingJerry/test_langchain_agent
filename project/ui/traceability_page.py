"""Reusable requirement/HTML evidence and case-version panels."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import streamlit as st


def _trace_rows(service: Any, project_id: str) -> dict[str, list[dict[str, Any]]]:
    try:
        return service.traceability_rows(project_id)
    except Exception as exc:
        st.warning(f"追踪数据暂时无法读取：{type(exc).__name__}: {exc}")
        return {}


def render_requirement_traceability_panel(service: Any, project_id: str) -> None:
    """Enhance uploaded requirements with structure and optional HTML evidence."""
    st.subheader("需求结构与 HTML 证据")
    st.caption(
        "这里不是另一套需求流程。请先在“资料上传”中上传文档；"
        "本区域只对已入库 DOCX 做 CSCI 结构化增强，并补充离线网页证据。"
    )
    documents = [
        row
        for row in service.document_summaries(project_id)
        if Path(str(row.get("file_path") or row.get("filename") or "")).suffix.lower()
        == ".docx"
    ]
    with st.container(border=True):
        st.write("**第一步：解析已上传的需求文档**")
        if not documents:
            st.info("尚无 DOCX。请先切换到“资料上传”上传软件需求规格说明书。")
        else:
            selected = st.selectbox(
                "选择已入库 DOCX",
                documents,
                format_func=lambda row: str(row.get("filename") or row.get("document_id")),
                key=f"csci_existing_doc_{project_id}",
            )
            if st.button(
                "识别 CSCI 层级与原子需求",
                type="primary",
                key=f"parse_existing_csci_{project_id}",
            ):
                source = Path(str(selected.get("file_path") or ""))
                if not source.exists():
                    st.error("已入库文档文件不存在，请重新上传该文档。")
                else:
                    result = service.analyze_csci_docx(project_id, source)
                    st.success(
                        f"识别 {len(result['nodes'])} 个最低级功能、"
                        f"{len(result['indicators'])} 条原子需求。"
                    )
                    st.rerun()

    with st.container(border=True):
        st.write("**第二步（可选）：补充离线 HTML 证据**")
        source_type = st.radio(
            "网页资料形式",
            ["单个 HTML", "多页面 ZIP"],
            horizontal=True,
            key=f"html_source_type_{project_id}",
        )
        if source_type == "单个 HTML":
            upload = st.file_uploader(
                "上传离线 HTML", type=["html", "htm"], key=f"offline_html_{project_id}"
            )
            if st.button("分析页面元素", disabled=not upload, key=f"parse_html_{project_id}"):
                result = service.analyze_offline_html(project_id, upload.name, upload.getvalue())
                st.success(
                    f"页面“{result['page']['title']}”提取 "
                    f"{len(result['elements'])} 个可交互元素。"
                )
                st.rerun()
        else:
            package = st.file_uploader(
                "上传离线站点 ZIP", type=["zip"], key=f"offline_zip_{project_id}"
            )
            if st.button(
                "安全解压并解析全部页面", disabled=not package, key=f"parse_zip_{project_id}"
            ):
                st.session_state[f"site_result_{project_id}"] = service.analyze_site_zip(
                    project_id, package.name, package.getvalue()
                )
            site = st.session_state.get(f"site_result_{project_id}")
            if site:
                st.caption(
                    f"站点包 {site['site_package_id']}：{len(site['pages'])} 页，"
                    f"缺失资源 {len(site['missing_resources'])} 项"
                )
                entry = st.selectbox(
                    "入口页面", site["entry_candidates"], key=f"site_entry_{project_id}"
                )
                with st.expander("高级：执行受限动态探索"):
                    st.warning("只执行明确填写的本地交互计划；外部网络请求会被阻止。")
                    plan_text = st.text_area(
                        "交互计划（JSON数组）",
                        value='[{"action":"input","locator":"#name","value":"测试用户"}]',
                        key=f"site_plan_{project_id}",
                    )
                    if st.button("执行探索", key=f"explore_site_{project_id}"):
                        try:
                            plan = json.loads(plan_text)
                            if not isinstance(plan, list):
                                raise ValueError("交互计划必须是 JSON 数组")
                            st.json(
                                service.explore_site_package(
                                    project_id, site["site_package_id"], entry, plan
                                )
                            )
                        except (ValueError, json.JSONDecodeError) as exc:
                            st.error(str(exc))

    rows = _trace_rows(service, project_id)
    labels = [
        ("requirement_hierarchy", "需求层级"),
        ("atomic_requirements", "原子需求"),
        ("html_elements", "HTML 元素"),
        ("requirement_element_links", "需求—元素匹配"),
        ("atomic_coverage_matrix", "覆盖矩阵"),
    ]
    available = [(key, label) for key, label in labels if rows.get(key)]
    if not available:
        st.info("完成需求结构解析后，这里会显示层级、原子需求和覆盖情况。")
        return
    for tab, (key, _label) in zip(st.tabs([label for _, label in available]), available):
        with tab:
            st.dataframe(rows[key], use_container_width=True, hide_index=True)


def render_case_optimization_panel(service: Any, project_id: str) -> None:
    """Single-case conversation and version workflow for the review page."""
    st.subheader("单条用例对话式优化")
    st.caption("只生成所选用例的新版本，不重新执行批量生成，也不会覆盖旧版本。")
    cases = service.manager.list_generated_cases(project_id)
    if not cases:
        st.info("当前项目尚无已生成用例，请先进入“生成测试用例”。")
        return
    by_id = {item["case_id"]: item for item in cases}
    case_id = st.selectbox("选择要优化的用例", list(by_id), key=f"regen_case_{project_id}")
    context = service.case_regeneration_context(project_id, case_id)
    with st.expander("查看该用例的需求、HTML证据和生成上下文"):
        st.json(context)
    feedback = st.text_area(
        "修改要求",
        placeholder="例如：不要编造数据库保存成功；补充手机号为空的异常步骤。",
        key=f"regen_feedback_text_{project_id}_{case_id}",
    )
    if st.button(
        "生成该用例的新版本",
        type="primary",
        disabled=not feedback.strip(),
        key=f"regen_submit_{project_id}_{case_id}",
    ):
        try:
            st.session_state[f"regen_result_{project_id}_{case_id}"] = (
                service.regenerate_single_case(project_id, case_id, feedback.strip())
            )
        except Exception as exc:
            st.error(f"单条用例优化失败，原用例未改变：{type(exc).__name__}: {exc}")
    result = st.session_state.get(f"regen_result_{project_id}_{case_id}")
    if result:
        changed = result["version"]["changed_fields"]
        st.write("**新旧版本差异**")
        st.dataframe(
            [
                {"字段": key, "旧值": result["original"].get(key), "新值": result["revised"].get(key)}
                for key in changed
            ],
            use_container_width=True,
            hide_index=True,
        )
        if result["coverage_gap_warning"]:
            st.warning(
                "接受后将出现原子需求唯一覆盖缺口："
                + ", ".join(result["coverage_gap_warning"])
            )
        version = int(result["version"]["version_no"])
        accept, reject = st.columns(2)
        if accept.button(
            "接受新版本", type="primary", key=f"accept_{project_id}_{case_id}_{version}"
        ):
            service.update_case_version(project_id, case_id, version, "accept")
            st.session_state.pop(f"regen_result_{project_id}_{case_id}", None)
            st.success("新版本已设为当前用例，旧版本仍保留。")
            st.rerun()
        if reject.button("拒绝新版本", key=f"reject_{project_id}_{case_id}_{version}"):
            service.update_case_version(project_id, case_id, version, "reject")
            st.session_state.pop(f"regen_result_{project_id}_{case_id}", None)
            st.info("已拒绝，当前用例没有改变。")
            st.rerun()

    versions = [
        row
        for row in _trace_rows(service, project_id).get("case_version_history", [])
        if row.get("case_id") == case_id
    ]
    if versions:
        with st.expander("版本历史与回滚"):
            st.dataframe(versions, use_container_width=True, hide_index=True)
            rollback = st.selectbox(
                "回滚目标版本",
                [row["version_no"] for row in versions],
                key=f"rollback_version_{project_id}_{case_id}",
            )
            if st.button(
                "创建并接受回滚版本", key=f"rollback_{project_id}_{case_id}_{rollback}"
            ):
                service.update_case_version(project_id, case_id, int(rollback), "rollback")
                st.success("已回滚；系统创建了新版本，历史版本未删除。")
                st.rerun()


def render_traceability_page(service: Any, project_id: str | None) -> None:
    """Backward-compatible wrapper; no longer a main navigation page."""
    st.header("需求与追踪")
    if not project_id:
        st.info("请先选择项目。")
        return
    render_requirement_traceability_panel(service, project_id)
    st.divider()
    render_case_optimization_panel(service, project_id)
