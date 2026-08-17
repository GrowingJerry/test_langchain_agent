"""Project workbench page."""

from typing import Optional

import streamlit as st


def render_workbench_page(service, project_id: Optional[str]) -> None:
    st.header("项目工作台")
    with st.expander("新建项目", expanded=not bool(project_id)):
        with st.form("new_project"):
            name = st.text_input("项目名称")
            description = st.text_area("项目说明")
            if st.form_submit_button("创建并进入项目"):
                try:
                    project = service.create_project(name, description)
                    st.session_state.pending_project_id = project["project_id"]
                    st.rerun()
                except ValueError:
                    st.error("请输入项目名称。")
    if not project_id:
        st.info("请先新建项目，然后按工作流上传资料、抽取需求并生成用例。")
        return
    project = service.get_project(project_id) or {}
    docs = service.list_documents(project_id)
    requirements = service.list_requirements(project_id)
    cases = service.list_generated_cases(project_id)
    profile = service.get_profile(project_id)
    scenario_cards = service.list_scenario_cards(project_id)
    traces = service.list_trace_sources(project_id)
    exports_dir = service.project_dir(project_id) / "exports"
    exported = exports_dir.exists() and any(exports_dir.iterdir())
    st.subheader(project.get("project_name", project_id))
    st.caption(project.get("description") or "暂无项目说明")
    cols = st.columns(4)
    for col, label, value in zip(
        cols,
        ["项目文档", "需求点", "测试用例", "追溯来源"],
        [len(docs), len(requirements), len(cases), len(traces)],
    ):
        col.metric(label, value)
    if profile:
        st.subheader("项目画像摘要")
        st.write(
            f"领域：{profile.get('domain') or '需人工确认'}　测试对象：{profile.get('test_object') or '需人工确认'}"
        )
        st.write(
            "主要功能：", "、".join(profile.get("main_functions") or []) or "尚未抽取"
        )
    steps = [
        ("上传资料", bool(docs)),
        ("抽取项目画像", bool(profile)),
        ("抽取需求", bool(requirements)),
        ("抽取场景卡片", bool(scenario_cards)),
        ("生成用例", bool(cases)),
        ("审查用例", bool(service.list_review_results(project_id))),
        ("导出文档", exported),
    ]
    st.subheader("流程状态")
    st.dataframe(
        [{"阶段": n, "状态": "已完成" if ok else "待处理"} for n, ok in steps],
        hide_index=True,
        use_container_width=True,
    )
    if exported:
        st.success("当前项目已完成资料、生成、审查和导出闭环。")
        st.info("下一步：可继续补充资料并重新生成，或切换其他项目。")
    elif cases and service.list_review_results(project_id):
        st.success("当前状态：用例已审查。")
        st.info("下一步：进入“导出中心”生成项目级 Excel 或 Word 文档。")
    elif cases:
        st.success("当前状态：已生成场景化用例。")
        st.info("下一步：进入“用例审查”修改用例、检查覆盖与来源。")
    elif requirements and scenario_cards:
        st.info("当前状态：需求与场景卡片已准备完成。")
        st.info("下一步：进入“生成测试用例”，选择需求后批量生成。")
    elif requirements:
        st.info("当前状态：已抽取需求。下一步进入“生成测试用例”；场景编译为可选高级流程。")
    elif docs:
        st.info(
            "当前状态：已上传资料。下一步进入“资料与需求”确认需求结构。"
        )
    else:
        st.warning("当前状态：未上传资料。")
        st.info("下一步：进入“资料与需求”上传并解析项目资料。")
