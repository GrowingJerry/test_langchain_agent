"""Project documents, equipment, learning, review, and retrieval diagnostics."""

from __future__ import annotations

import json
from typing import Any, Optional

import streamlit as st

from workflows.learning.requirement_extractor import classify_requirement

IMAGE_TYPES = ["png", "jpg", "jpeg", "webp"]


def _table_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def _table_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _table_value(value) for key, value in row.items()} for row in rows]


def _csv_values(value: str) -> list[str]:
    return list(dict.fromkeys(
        item.strip() for item in value.replace("，", ",").split(",") if item.strip()
    ))


def _render_document_jobs(service: Any, project_id: str) -> None:
    jobs = service.list_document_jobs(project_id)
    if not jobs:
        return
    st.subheader("长文档后台任务")
    for job in jobs:
        with st.container(border=True):
            st.write(f"**{job['job_id']}** · {job['status']} · {job.get('stage') or '-'}")
            st.progress(float(job.get("progress") or 0), text=(
                f"第 {job.get('current_page', 0)} / {job.get('total_pages', 0)} 页"
            ))
            if job.get("error_message"):
                st.error(job["error_message"])
            left, middle, right = st.columns(3)
            if left.button("暂停", key=f"pause_{job['job_id']}"):
                service.pause_document_job(project_id, job["job_id"])
                st.rerun()
            if middle.button("恢复/重试", key=f"resume_{job['job_id']}"):
                service.resume_document_job(project_id, job["job_id"])
                st.rerun()
            if right.button("取消", key=f"cancel_{job['job_id']}"):
                service.cancel_document_job(project_id, job["job_id"])
                st.rerun()


def _render_visual_evidence_area(service: Any, project_id: str) -> None:
    with st.expander("多模态资料 / 图片证据抽取"):
        st.info("视觉识别结果仅作为测试设计证据，需要人工确认。")
        images = st.file_uploader(
            "上传截图、流程图、扫描页或设备界面图", type=IMAGE_TYPES,
            accept_multiple_files=True, key=f"visual_assets_{project_id}",
        )
        if st.button("上传并抽取视觉证据", disabled=not images,
                     key=f"extract_visual_{project_id}"):
            for image in images or []:
                result = service.process_visual_upload(project_id, image.name, image.getvalue())
                if result.get("ok"):
                    st.success(f"{image.name} 已保存：{result['evidence_id']}")
                    st.json(result.get("data") or {})
                else:
                    st.warning(f"{image.name} 抽取失败：{result.get('error')}")


def _render_project_documents(service: Any, project_id: str, case_library: Any) -> None:
    files = st.file_uploader(
        "上传项目说明书、需求、接口、标准或技术资料",
        type=["txt", "md", "docx", "pdf"], accept_multiple_files=True,
        key=f"docs_{project_id}",
    )
    if st.button("上传、解析并入库", disabled=not files, type="primary",
                 key=f"ingest_{project_id}"):
        for item in files or []:
            try:
                result = service.ingest_document(project_id, item.name, item.getvalue())
                if result.get("background"):
                    st.info(f"{item.name} 已进入后台任务：{result['job_id']}")
                else:
                    message = f"{result['filename']}：{result['chunk_count']} 个片段"
                    (st.warning if result.get("warning") else st.success)(
                        f"{message} {result.get('warning', '')}"
                    )
            except ValueError as exc:
                st.error(f"{item.name} 上传失败：{exc}")
        st.rerun()

    _render_document_jobs(service, project_id)
    documents = service.document_summaries(project_id)
    if documents:
        st.subheader("项目文档")
        summary = [{
            "document_id": row["document_id"], "文件名": row["filename"],
            "解析器": row.get("parser", ""), "页数": row.get("total_pages", 0),
            "OCR页数": row.get("ocr_processed_pages", 0),
            "OCR低置信页": row.get("ocr_low_confidence_pages", 0),
            "片段数": row.get("chunk_count", 0),
            "状态": row.get("processing_status", ""),
            "警告": row.get("warnings", []),
        } for row in documents]
        st.dataframe(_table_rows(summary), hide_index=True, use_container_width=True)
        selected = st.selectbox(
            "文档维护", documents, format_func=lambda row: row["filename"],
            key=f"maintain_doc_{project_id}",
        )
        if selected.get("warnings"):
            st.warning("\n".join(selected["warnings"]))
        if st.button("删除选中文档及片段", key=f"delete_doc_{project_id}"):
            service.delete_document(project_id, selected["document_id"])
            st.success("资料记录与片段已删除，原始上传文件保留。")
            st.rerun()

    with st.expander("手动补充需求文本"):
        manual = st.text_area("需求文本（每行一条）", key=f"manual_req_{project_id}")
        if st.button("加入当前项目需求", key=f"add_manual_req_{project_id}"):
            sequence = len(service.list_requirements(project_id)) + 1
            for line in [part.strip() for part in manual.splitlines() if part.strip()]:
                service.upsert_requirement(project_id, {
                    "requirement_id": f"REQ-M-{sequence:03d}", "title": line[:48],
                    "description": line, "category": classify_requirement(line),
                    "source_document": "manual_input",
                })
                sequence += 1
            st.success("需求已加入当前项目。")
    _render_visual_evidence_area(service, project_id)

    with st.expander("历史用例库（仅作格式和方法参考）"):
        if case_library is None:
            st.info("历史用例库未启用。")
        else:
            upload = st.file_uploader(
                "导入 Excel / CSV / JSON", type=["xlsx", "csv", "json"],
                key=f"history_upload_{project_id}",
            )
            if upload and st.button("导入历史用例库", key=f"import_history_{project_id}"):
                target = service.save_history_upload(project_id, upload.name, upload.getvalue())
                count = (case_library.import_excel(target) if target.suffix.lower() == ".xlsx"
                         else case_library.import_csv(target) if target.suffix.lower() == ".csv"
                         else case_library.import_json(target))
                st.success(f"已导入 {count} 条。")


def _render_equipment_data(service: Any, project_id: str) -> None:
    st.subheader("JSONL装备数据导入")
    scope = st.radio("导入作用域", ["当前项目", "GLOBAL"], horizontal=True,
                     key=f"equipment_scope_{project_id}")
    upload = st.file_uploader("上传装备JSONL", type=["jsonl"],
                              key=f"equipment_jsonl_{project_id}")
    if st.button("流式导入装备数据", disabled=not upload,
                 key=f"import_equipment_{project_id}"):
        report = service.import_equipment_jsonl(
            project_id, upload.name, upload.getvalue(),
            "GLOBAL" if scope == "GLOBAL" else project_id,
        )
        st.session_state[f"equipment_report_{project_id}"] = report
    report = st.session_state.get(f"equipment_report_{project_id}")
    if report:
        cols = st.columns(6)
        for column, (label, key) in zip(cols, (
            ("总行数", "total_lines"), ("成功", "success_count"),
            ("跳过", "skipped_count"), ("重复", "duplicate_count"),
            ("失败", "failure_count"), ("缺少名称", "missing_name_count"),
        )):
            column.metric(label, report.get(key, 0))
        st.write("字段覆盖", report.get("field_coverage", {}))
        if report.get("errors"):
            st.dataframe(_table_rows(report["errors"]), hide_index=True,
                         use_container_width=True)

    st.subheader("装备检索")
    one, two, three, four = st.columns(4)
    name = one.text_input("名称或别名", key=f"eq_name_{project_id}")
    category = two.text_input("类别", key=f"eq_category_{project_id}")
    role = three.text_input("角色", key=f"eq_role_{project_id}")
    capability = four.text_input("能力", key=f"eq_capability_{project_id}")
    allow_global = st.checkbox("同时检索GLOBAL装备", key=f"eq_global_{project_id}")
    if st.button("检索装备", key=f"search_equipment_{project_id}"):
        result = service.search_equipment_ui(
            project_id, name=name, category=category, role=role,
            capability=capability, allow_global=allow_global,
        )
        st.session_state[f"equipment_search_{project_id}"] = result
    result = st.session_state.get(f"equipment_search_{project_id}")
    if result:
        if result.get("missing_information"):
            st.warning("；".join(result["missing_information"]))
        for label, key in (("匹配装备", "matches"), ("未通过约束的候选", "rejected_matches")):
            st.write(f"**{label}**")
            rows = result.get(key) or []
            if rows:
                st.dataframe(_table_rows(rows), hide_index=True, use_container_width=True)
                for row in rows:
                    with st.expander(f"{row['name']} · 原始记录来源"):
                        st.json(row.get("source_refs") or [])


def _render_scenario_learning(service: Any, project_id: str) -> None:
    documents = service.document_summaries(project_id)
    st.subheader("创建项目领域学习任务")
    with st.form(f"learning_form_{project_id}"):
        task_name = st.text_input("任务名称")
        learning_goal = st.text_area("学习目标")
        domain = st.text_input("领域")
        simulation_object = st.text_input("仿真对象")
        selected_documents = st.multiselect(
            "选择资料", documents, format_func=lambda row: row["filename"],
        )
        target_subsystems = st.text_input("目标子系统（逗号分隔）")
        target_topics = st.text_input("目标主题（逗号分隔）")
        scenario_types = st.text_input("预期场景类型（逗号分隔）")
        excluded_topics = st.text_input("排除主题（逗号分隔）")
        submitted = st.form_submit_button("创建并启动知识抽取", type="primary")
    if submitted:
        try:
            task = service.create_learning_task(
                project_id, task_name=task_name, learning_goal=learning_goal,
                domain=domain, simulation_object=simulation_object,
                target_subsystems=_csv_values(target_subsystems),
                target_topics=_csv_values(target_topics),
                expected_scenario_types=_csv_values(scenario_types),
                excluded_topics=_csv_values(excluded_topics),
                selected_document_ids=[row["document_id"] for row in selected_documents],
            )
            result = service.run_learning_task(project_id, task.task_id)
            st.session_state[f"learning_result_{project_id}"] = result
        except ValueError as exc:
            st.error(str(exc))
    result = st.session_state.get(f"learning_result_{project_id}")
    if result:
        if result.get("status") == "failed":
            st.error("\n".join(result.get("errors") or ["知识抽取失败"]))
        else:
            report = result.get("report") or {}
            st.success("知识抽取完成，知识仍需按审核状态使用。")
            st.write("**文档覆盖**")
            st.dataframe(_table_rows(report.get("document_coverage") or []),
                         hide_index=True, use_container_width=True)
            st.write("**知识类型覆盖**", report.get("knowledge_type_coverage") or {})
            st.write("**场景能力覆盖**", report.get("scenario_capability_coverage") or {})
            if report.get("missing_topics"):
                st.warning("缺失主题：" + "、".join(report["missing_topics"]))
            st.metric("待审核知识", report.get("pending_review_count", 0))
    tasks = service.list_learning_tasks(project_id)
    if tasks:
        st.subheader("学习任务记录")
        st.dataframe(_table_rows(tasks), hide_index=True, use_container_width=True)


def _render_knowledge_review(service: Any, project_id: str) -> None:
    st.subheader("知识筛选与审核")
    one, two, three, four = st.columns(4)
    status = one.selectbox("状态", ["", "draft", "reviewed", "approved", "rejected", "deprecated"])
    knowledge_type = two.selectbox("类型", ["", "terminology", "subsystem", "simulation_model",
        "state_variable", "input_output", "state_transition", "environment_factor",
        "fault_mode", "parameter", "constraint", "verification_rule", "scenario_pattern"])
    source_kind = three.selectbox("来源", ["", "formal_document", "human_feedback",
        "organization_template", "book", "historical_case"])
    only_conflicts = four.checkbox("仅显示冲突知识")
    if st.button("重新检测冲突", key=f"detect_conflicts_{project_id}"):
        service.detect_knowledge_conflicts(project_id)
        st.rerun()
    rows = service.list_knowledge_for_review(
        project_id, status=status, knowledge_type=knowledge_type,
        source_kind=source_kind, only_conflicts=only_conflicts,
    )
    conflicts = service.list_knowledge_conflicts(project_id)
    if conflicts:
        with st.expander(f"冲突记录（{len(conflicts)}）"):
            st.dataframe(_table_rows(conflicts), hide_index=True, use_container_width=True)
    if not rows:
        st.info("没有符合筛选条件的知识。")
        return
    selected = st.selectbox(
        "选择知识", rows,
        format_func=lambda row: f"[{row['status']}] {row['knowledge_type']} · {row.get('title') or row['knowledge_unit_id']}",
        key=f"review_unit_{project_id}",
    )
    st.write(selected.get("content") or "")
    st.caption(
        f"来源：document={selected.get('document_id') or '-'} · "
        f"chunk={selected.get('chunk_id') or '-'} · 页码={selected.get('page_no') or '-'} · "
        f"章节={selected.get('section_scope') or '-'}"
    )
    normalized = selected.get("normalized_data") or {}
    if selected["knowledge_type"] == "parameter":
        st.warning("参数知识：批准前必须核对数值、单位、运行条件和值类型。")
        columns = st.columns(4)
        columns[0].metric("值", normalized.get("value", "-"))
        columns[1].metric("单位", normalized.get("unit") or "缺失")
        columns[2].metric("运行条件", normalized.get("operating_condition") or "缺失")
        columns[3].metric("值类型", normalized.get("value_type") or "缺失")
    st.json(normalized)
    reviewer = st.text_input("审核人", key=f"reviewer_{project_id}")
    comments = st.text_area("审核意见", key=f"review_comments_{project_id}")
    corrections_text = st.text_area(
        "修改字段（JSON对象，可留空）", value="{}", key=f"review_corrections_{project_id}",
    )
    approve, reject, modify, deprecate = st.columns(4)

    def apply_review(new_status: str) -> None:
        try:
            corrections = json.loads(corrections_text or "{}")
            if not isinstance(corrections, dict):
                raise ValueError("修改字段必须是JSON对象")
            service.review_knowledge(
                project_id, selected["knowledge_unit_id"], new_status=new_status,
                reviewer=reviewer, comments=[comments] if comments else [],
                corrections=corrections,
            )
            st.rerun()
        except (ValueError, json.JSONDecodeError) as exc:
            st.error(str(exc))

    if approve.button("批准", key=f"approve_{project_id}"):
        apply_review("approved")
    if reject.button("驳回", key=f"reject_{project_id}"):
        apply_review("rejected")
    if modify.button("修改并复核", key=f"modify_{project_id}"):
        apply_review("reviewed")
    if deprecate.button("废弃", key=f"deprecate_{project_id}"):
        apply_review("deprecated")


def _render_retrieval_debug(service: Any, project_id: str, top_k: int) -> None:
    st.info("本页只执行只读查询，不修改项目数据。")
    query = st.text_input("调试查询", key=f"retrieval_debug_query_{project_id}")
    if st.button("执行四路检索", disabled=not query.strip(),
                 key=f"retrieval_debug_run_{project_id}"):
        st.session_state[f"retrieval_debug_{project_id}"] = service.debug_retrieval(
            project_id, query, top_k
        )
    result = st.session_state.get(f"retrieval_debug_{project_id}")
    if not result:
        return
    for title, key in (
        ("项目文档", "documents"), ("Approved知识", "approved_knowledge"),
        ("Approved场景模板", "scenario_templates"), ("装备候选", "equipment_candidates"),
    ):
        st.subheader(title)
        rows = result.get(key) or []
        if rows:
            st.dataframe(_table_rows(rows), hide_index=True, use_container_width=True)
        else:
            st.caption("没有匹配结果。")
    if result.get("equipment_missing_information"):
        st.warning("；".join(result["equipment_missing_information"]))


def render_knowledge_page(
    service: Any, project_id: Optional[str], case_library: Any, top_k: int
) -> None:
    st.header("文档与知识库")
    if not project_id:
        st.info("请先在项目工作台创建项目。")
        return
    tabs = st.tabs(["项目文档", "装备数据", "场景学习", "知识审核", "检索调试"])
    with tabs[0]:
        _render_project_documents(service, project_id, case_library)
    with tabs[1]:
        _render_equipment_data(service, project_id)
    with tabs[2]:
        _render_scenario_learning(service, project_id)
    with tabs[3]:
        _render_knowledge_review(service, project_id)
    with tabs[4]:
        _render_retrieval_debug(service, project_id, top_k)
