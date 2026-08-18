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
        st.write("**步骤2：识别 3.2 子系统概述与 3.3 详细功能**")
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
                "识别 3.2/3.3 完整需求树",
                type="primary",
                key=f"parse_existing_csci_{project_id}",
            ):
                source = Path(str(selected.get("file_path") or ""))
                if not source.exists():
                    st.error("已入库文档文件不存在，请重新上传该文档。")
                else:
                    result = service.analyze_csci_docx(project_id, source)
                    st.success(
                        f"保存完整树 {len(result['nodes'])} 个节点，其中 "
                        f"{len(result['testable_nodes'])} 个最深可测试功能。"
                    )
                    st.rerun()

    with st.container(border=True):
        st.write("**步骤3：功能描述无损拆分与覆盖审核**")
        if st.button("调用配置模型拆分并独立审计",type="primary",key=f"atomize_audit_{project_id}"):
            with st.status("正在拆分功能描述并执行独立覆盖审计",expanded=True): result=service.atomize_and_audit_requirements(project_id)
            st.session_state[f"atom_audit_{project_id}"]=result
        audit=st.session_state.get(f"atom_audit_{project_id}")
        if audit: st.write("覆盖审计",audit)
        atom_rows=_trace_rows(service,project_id).get("atomic_requirements",[])
        if atom_rows:
            editable=st.data_editor(atom_rows,hide_index=True,use_container_width=True,num_rows="dynamic",key=f"atom_review_{project_id}")
            if st.button("保存人工审核结果",key=f"save_atoms_{project_id}"):
                service.save_reviewed_atoms(project_id,editable.to_dict("records") if hasattr(editable,"to_dict") else list(editable)); st.success("已保存；拆分、合并和遗漏补充可通过增删及编辑行完成。")

    with st.container(border=True):
        st.write("**步骤4：HTML 站点静态分析；步骤5：Playwright 自动探索**")
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
                health=service.playwright_status()
                if health.get("available"):
                    st.success(f"Playwright 可用，Chromium {health.get('chromium_version','')}")
                else:
                    st.warning(f"动态探索不可用：{health.get('status')}。{health.get('repair','')}")
                if st.button("自动理解站点并安全探索",type="primary",disabled=not health.get("available"),key=f"explore_site_{project_id}"):
                    with st.status("正在自动理解页面并执行安全探索",expanded=True):
                        result=service.auto_explore_site_package(project_id,site["site_package_id"],entry)
                    st.session_state[f"explore_result_{project_id}"]=result; st.success(f"完成 {len(result.get('events',[]))} 个观测动作。")
                result=st.session_state.get(f"explore_result_{project_id}")
                if result:
                    st.write("自动探索摘要",{"动作数":len(result.get("events",[])),"外部请求阻止数":len(result.get("blocked_requests",[])),"最终页面":result.get("final_url","")})
                    with st.expander("高级/调试：原始探索证据"): st.json(result)

    with st.container(border=True):
        st.write("**步骤6：需求—页面语义绑定**")
        st.caption("系统结合需求、简化DOM和Playwright观测自动绑定；关键词只用于召回候选。")
        if st.button("自动理解站点并绑定需求",type="primary",key=f"semantic_bind_{project_id}"):
            with st.status("正在理解需求与页面语义",expanded=True): binding=service.auto_bind_requirements(project_id)
            st.session_state[f"binding_result_{project_id}"]=binding
        binding=st.session_state.get(f"binding_result_{project_id}")
        if binding:
            st.write({"自动确认":binding["confirmed"],"待人工确认":binding["need_human_confirm"],"模型":binding["model"]})
            st.dataframe(binding["bindings"],use_container_width=True,hide_index=True)

        review_rows = _trace_rows(service, project_id)
        bindings = list(review_rows.get("requirement_page_links", [])) + list(
            review_rows.get("requirement_element_links", [])
        )
        if bindings:
            for row in bindings:
                row["confirmed"] = row.get("status") == "confirmed"
            st.caption("人工确认或调整页面/元素绑定；此处不要求填写 CSS/XPath 等技术定位字段。")
            editable_bindings = st.data_editor(
                bindings,
                hide_index=True,
                use_container_width=True,
                disabled=["link_id", "binding_type", "function_id", "indicator_id", "confidence", "reason"],
                key=f"binding_review_{project_id}",
            )
            if st.button("保存绑定确认", key=f"save_binding_review_{project_id}"):
                service.save_binding_reviews(
                    project_id,
                    editable_bindings.to_dict("records")
                    if hasattr(editable_bindings, "to_dict")
                    else list(editable_bindings),
                )
                st.success("绑定确认已保存。")
                st.rerun()

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
