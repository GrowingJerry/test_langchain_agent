"""Reusable requirement/HTML evidence and case-version panels."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import streamlit as st

logger = logging.getLogger("test_agent.traceability_ui")


def _trace_rows(service: Any, project_id: str) -> dict[str, list[dict[str, Any]]]:
    try:
        return service.traceability_rows(project_id)
    except Exception as exc:
        logger.exception("Traceability data load failed project=%s",project_id)
        st.warning(f"追踪数据暂时无法读取：{type(exc).__name__}: {exc}")
        return {}


def render_requirement_traceability_panel(service: Any, project_id: str) -> None:
    """Enhance uploaded requirements with structure and optional HTML evidence."""
    st.subheader("需求结构与 HTML 证据")
    st.caption(
        "这里不是另一套需求流程。请先在“资料上传”中上传文档；"
        "本区域只对已入库 DOCX 做 CSCI 结构化增强，并补充离线网页证据。"
    )
    flow = service.workflow_status(project_id)
    st.write("**流程状态**")
    st.write({
        "1 文档已上传": flow["document_uploaded"],
        "2 需求树待确认": bool(flow["lowest_function_count"] and not flow["atom_review_passed"]),
        "3 原子需求待确认": bool(flow["atom_count"] and not flow["atom_review_passed"]),
        "4 HTML已分析": bool(flow["page_count"]),
        "5 页面绑定待确认": bool(flow["pending_confirmation"]),
        "6 可进入生成": bool(flow["atom_review_passed"] and flow["binding_completion"] > 0),
    })
    documents = [
        row
        for row in service.document_summaries(project_id)
        if Path(str(row.get("file_path") or row.get("filename") or "")).suffix.lower()
        == ".docx"
    ]
    with st.container(border=True):
        st.write("**步骤2：识别并审核需求层级与最低可测试功能**")
        st.info("机器将识别层级、功能描述、输入、处理和输出。请检查层级归属及四部分是否属于同一最低功能；不通过可修改或退回重新解析，确认后进入原子拆分。")
        if not documents:
            st.info("尚无 DOCX。请先切换到“资料上传”上传软件需求规格说明书。")
        else:
            selected = st.selectbox(
                "选择已入库 DOCX",
                documents,
                format_func=lambda row: str(row.get("filename") or row.get("document_id")),
                key=f"csci_existing_doc_{project_id}",
            )
            extraction_mode=st.radio("需求抽取方式",["自动识别抽取方式（推荐）","规范CSCI结构抽取","通用AI需求抽取"],horizontal=True,key=f"requirement_extraction_mode_{project_id}")
            if st.button(
                "抽取并写入统一需求树",
                type="primary",
                key=f"parse_existing_csci_{project_id}",
            ):
                source = Path(str(selected.get("file_path") or ""))
                if not source.exists():
                    st.error("已入库文档文件不存在，请重新上传该文档。")
                else:
                    progress=st.progress(0,text="阶段 1/3：读取 DOCX 有序正文块")
                    started=time.monotonic()
                    try:
                        progress.progress(.35,text="阶段 2/3：按标题语义和层级确定性识别最低功能")
                        mode={"自动识别抽取方式（推荐）":"auto","规范CSCI结构抽取":"csci","通用AI需求抽取":"general"}[extraction_mode]
                        def extraction_progress(event):
                            progress.progress(min(.85,max(.1,float(event.get('index',0))/max(1,float(event.get('total',1))))),text=f"{event.get('stage','处理中')}：{event.get('object','')} {event.get('index',0)}/{event.get('total',1)}")
                        result = service.extract_requirement_document(project_id, source,mode,extraction_progress)
                        progress.progress(1.0,text=f"阶段 3/3：持久化完成，用时 {result['elapsed_seconds']:.2f} 秒")
                        summary={key:result[key] for key in ("section_32_count","section_33_count","testable_count","warnings","elapsed_seconds")}
                        st.session_state[f"csci_summary_{project_id}"]=summary
                        if result["node_count"] == 0:
                            st.error("解析结果为 0，已阻断模型拆分。请检查文档是否包含编号明确的 3.2/3.3 标题。")
                        else:
                            st.success(f"需求树解析完成：采用 {result['extraction_method']}；CSCI能力需求节点 {result['section_32_count']}，CSCI能力节点 {result['section_33_count']}，最低可测功能 {result['testable_count']}，用时 {result['elapsed_seconds']:.2f} 秒。")
                            if result.get('fallback_reason'): st.warning(result['fallback_reason'])
                        for warning in result["warnings"]: st.warning(warning)
                    except Exception as exc:
                        logger.exception("CSCI parse button failed project=%s",project_id)
                        progress.empty(); st.error(f"需求树解析失败：{type(exc).__name__}: {exc}。详情见 logs/streamlit.log。")

    with st.container(border=True):
        st.write("**步骤3：功能描述无损拆分与覆盖审核**")
        st.info("请对照原始功能描述检查遗漏、重复、拆分过粗和模型编造。可编辑、增删行完成补充、拆分或合并；保存后原子需求进入 HTML 绑定和用例覆盖计划。")
        workflow=service.workflow_status(project_id)
        no_testable=workflow.get("lowest_function_count",0)==0
        if no_testable: st.warning("最低可测功能数为 0，模型拆分已禁用。请先修复需求树解析结果。")
        if st.button("调用配置模型拆分并独立审计",type="primary",disabled=no_testable,key=f"atomize_audit_{project_id}"):
            bar=st.progress(0,text="准备逐项拆分")
            current=st.empty()
            def on_progress(event):
                index=int(event.get("index",0)); total=max(1,int(event.get("total",1)))
                current.info(f"[{index}/{total}] {event.get('function_id','')} {event.get('name','')}：{event.get('status','')}")
                bar.progress(min(index/total,1.0),text=f"已处理 {index}/{total}")
            try:
                result=service.atomize_and_audit_requirements(project_id,progress_callback=on_progress,resume=True)
                summary={"total":result["total"],"completed":result["completed"],"failure_count":len(result["failures"]),"failures":result["failures"],"coverage_complete":result["coverage_complete"]}
                st.session_state[f"atom_audit_{project_id}"]=summary
                if result["failures"] or not result["functions"]:
                    st.error(f"拆分未完整完成：失败 {len(result['failures'])} 项；空结果不会被视为成功。")
                else: st.success(f"逐项拆分完成，本次完成 {result['completed']} 项；已完成项支持断点跳过。")
            except Exception as exc:
                logger.exception("Atomization button failed project=%s",project_id)
                st.error(f"模型拆分失败：{type(exc).__name__}: {exc}。已完成项已保存，可再次点击断点续跑。")
        audit=st.session_state.get(f"atom_audit_{project_id}")
        if audit: st.write("覆盖审计",audit)
        atom_rows=_trace_rows(service,project_id).get("atomic_requirements",[])
        if atom_rows:
            editable=st.data_editor(atom_rows,hide_index=True,use_container_width=True,num_rows="dynamic",key=f"atom_review_{project_id}")
            if st.button("保存人工审核结果",key=f"save_atoms_{project_id}"):
                try:
                    service.save_reviewed_atoms(project_id,editable.to_dict("records") if hasattr(editable,"to_dict") else list(editable)); st.success("已保存；拆分、合并和遗漏补充可通过增删及编辑行完成。")
                except Exception as exc:
                    logger.exception("Saving reviewed atoms failed project=%s",project_id); st.error(f"保存审核结果失败：{type(exc).__name__}: {exc}")

    with st.container(border=True):
        st.write("**步骤4：HTML 站点静态分析；步骤5：Playwright 自动探索**")
        st.info("通常只需确认页面、业务区域、关键控件、未命名/不唯一控件和 Playwright 异常，不需要逐个审核完整 DOM。")
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
            if st.button("分析网页并采集证据", disabled=not upload, key=f"parse_html_{project_id}"):
                progress=st.progress(0,text="1. 保存站点资料；2. 源码静态扫描；3. 页面类型判断")
                try:
                    result = service.analyze_offline_html(project_id, upload.name, upload.getvalue(),include_elements=False)
                    if result.get("requires_browser_render"):
                        progress.progress(.5,text="检测到 JavaScript 动态渲染页面；正在使用 Chromium 获取渲染后页面证据")
                    progress.progress(1.0,text="证据采集流程结束")
                    summary={"页面类型":result.get("page_type"),"源码元素数":result.get("source_element_count"),"渲染后元素数":result.get("rendered_element_count"),"表单数":result.get("form_count"),"按钮数":result.get("button_count"),"输入控件数":result.get("input_count"),"页面ID":result.get("page",{}).get("page_id"),"状态":result.get("status"),"耗时":result.get("elapsed_seconds")}
                    if result.get("status") in {"completed","partial_success"}: st.success(summary)
                    else: st.warning(summary); st.error(result.get("failure_reason") or "浏览器渲染证据未完成；源码扫描结果已保留。")
                    with st.expander("查看页面与证据详情"):
                        st.json({"site_package_id":result.get("site_package_id"),"page":result.get("page"),"stages":result.get("stages"),"browser_rendered":result.get("browser_rendered",{})})
                    for warning in result["warnings"]: st.warning(warning)
                except Exception as exc:
                    logger.exception("Static HTML button failed project=%s file=%s",project_id,getattr(upload,"name",""))
                    progress.empty(); st.error(f"HTML 静态分析失败：{type(exc).__name__}: {exc}。详情见 logs/streamlit.log。")
        else:
            package = st.file_uploader(
                "上传离线站点 ZIP", type=["zip"], key=f"offline_zip_{project_id}"
            )
            if st.button(
                "安全解压并解析全部页面", disabled=not package, key=f"parse_zip_{project_id}"
            ):
                try:
                    site_result=service.analyze_site_zip(project_id,package.name,package.getvalue())
                    st.session_state[f"site_result_{project_id}"]={"site_package_id":site_result["site_package_id"],"entry_candidates":site_result["entry_candidates"],"page_count":len(site_result["pages"]),"missing_count":len(site_result["missing_resources"])}
                except Exception as exc:
                    logger.exception("ZIP analysis failed project=%s",project_id)
                    st.error(f"ZIP 分析失败：{type(exc).__name__}: {exc}")
            site = st.session_state.get(f"site_result_{project_id}")
            if site:
                st.caption(
                    f"站点包 {site['site_package_id']}：{site['page_count']} 页，"
                    f"缺失资源 {site['missing_count']} 项"
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
                    try:
                        with st.status("正在独立子进程中执行安全探索",expanded=True): result=service.auto_explore_site_package(project_id,site["site_package_id"],entry)
                        summary={"event_count":len(result.get("events",[])),"blocked_count":len(result.get("blocked_requests",[])),"final_url":result.get("final_url",""),"return_code":result.get("return_code",0),"failure_reason":result.get("failure_reason","")}
                        st.session_state[f"explore_result_{project_id}"]=summary
                        if summary["return_code"] or summary["failure_reason"]: st.error(f"动态探索失败：{summary['failure_reason']}")
                        else: st.success(f"完成 {summary['event_count']} 个观测动作。")
                    except Exception as exc:
                        logger.exception("Playwright exploration failed project=%s",project_id)
                        st.error(f"动态探索子进程失败：{type(exc).__name__}: {exc}。Streamlit 后端仍保持运行。")
                result=st.session_state.get(f"explore_result_{project_id}")
                if result:
                    st.write("自动探索摘要",result)

    with st.container(border=True):
        st.write("**步骤6：需求—页面语义绑定**")
        st.caption("系统结合需求、简化DOM和Playwright观测推荐页面及元素；请检查绑定理由、置信度和待确认原因。关键词只用于召回候选。")
        funnel=service.binding_funnel(project_id); st.write("绑定数据漏斗",funnel)
        binding_disabled=funnel["最低可测功能"]==0 or funnel["HTML页面"]==0
        if funnel["最低可测功能"]==0: st.warning("最低可测功能为0，绑定未执行。")
        if funnel["HTML页面"]==0: st.warning("HTML页面为0，请先完成静态分析。")
        if st.button("自动理解站点并绑定需求",type="primary",disabled=binding_disabled,key=f"semantic_bind_{project_id}"):
            try:
                with st.status("正在理解需求与页面语义",expanded=True): binding=service.auto_bind_requirements(project_id)
                st.session_state[f"binding_result_{project_id}"]={"participating":binding["participating"],"confirmed":binding["confirmed"],"need_human_confirm":binding["need_human_confirm"],"unmatched":binding["unmatched"],"failed":binding["failed"],"model":binding["model"],"bindings":binding["bindings"][:100]}
            except Exception as exc:
                logger.exception("Semantic binding failed project=%s",project_id)
                st.error(f"语义绑定失败：{type(exc).__name__}: {exc}")
        binding=st.session_state.get(f"binding_result_{project_id}")
        if binding:
            st.write({"参与绑定":binding["participating"],"自动确认":binding["confirmed"],"待人工确认":binding["need_human_confirm"],"未匹配":binding["unmatched"],"失败":binding["failed"],"模型":binding["model"]})
            st.dataframe(binding["bindings"],use_container_width=True,hide_index=True)

        review_rows = _trace_rows(service, project_id)
        bindings = list(review_rows.get("requirement_page_links", [])) + list(
            review_rows.get("requirement_element_links", [])
        )
        if bindings:
            for row in bindings:
                row["confirmed"] = row.get("status") == "confirmed"
                row["page_confirmed_element_pending"] = row.get("status") == "page_confirmed_element_pending"
            st.caption("页面相关但暂不能确定单个元素时，对页面行勾选 page_confirmed_element_pending；无需填写 CSS/XPath/locator。明确不相关页面仍禁止确认。")
            editable_bindings = st.data_editor(
                bindings,
                hide_index=True,
                use_container_width=True,
                disabled=["link_id", "binding_type", "function_id", "indicator_id", "confidence", "reason"],
                key=f"binding_review_{project_id}",
            )
            if st.button("保存绑定确认", key=f"save_binding_review_{project_id}"):
                try:
                    service.save_binding_reviews(project_id,editable_bindings.to_dict("records") if hasattr(editable_bindings,"to_dict") else list(editable_bindings))
                    st.success("绑定确认已保存。")
                    st.rerun()
                except Exception as exc:
                    logger.exception("Saving binding review failed project=%s",project_id); st.error(f"保存绑定确认失败：{type(exc).__name__}: {exc}")

    rows = _trace_rows(service, project_id)
    labels = [
        ("requirement_hierarchy", "需求层级"),
        ("atomic_requirements", "原子需求"),
        ("requirement_element_links", "需求—元素匹配"),
        ("atomic_coverage_matrix", "覆盖矩阵"),
    ]
    available = [(key, label) for key, label in labels if rows.get(key)]
    if not available:
        st.info("完成需求结构解析后，这里会显示层级、原子需求和覆盖情况。")
    else:
        for tab, (key, _label) in zip(st.tabs([label for _, label in available]), available):
            with tab:
                st.dataframe(rows[key], use_container_width=True, hide_index=True)

    element_page=st.number_input("HTML 元素页码",min_value=1,value=1,step=1,key=f"html_element_page_{project_id}")
    element_result=service.list_html_elements_page(project_id,int(element_page),100)
    if element_result["total"]:
        st.caption(f"HTML 元素共 {element_result['total']} 项；当前第 {element_result['page']}/{element_result['pages']} 页，每页最多 100 项。")
        st.dataframe(element_result["rows"],use_container_width=True,hide_index=True)


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
