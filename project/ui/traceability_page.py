"""Streamlit review UI for CSCI and offline-HTML traceability."""
from pathlib import Path
import tempfile
import streamlit as st


def render_traceability_page(service, project_id: str | None) -> None:
    st.header("需求结构与离线 HTML 追踪")
    if not project_id:
        st.info("请先选择项目。")
        return
    left, right = st.columns(2)
    with left:
        upload = st.file_uploader("上传软件需求规格说明书（DOCX）", type=["docx"], key=f"csci_docx_{project_id}")
        if upload and st.button("解析 CSCI 需求结构", key=f"parse_csci_{project_id}"):
            with tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / upload.name
                target.write_bytes(upload.getvalue())
                result = service.analyze_csci_docx(project_id, target)
            st.success(f"识别 {len(result['nodes'])} 个最低级功能、{len(result['indicators'])} 条原子需求。")
    with right:
        upload = st.file_uploader("上传离线 HTML", type=["html", "htm"], key=f"offline_html_{project_id}")
        if upload and st.button("静态分析 HTML", key=f"parse_html_{project_id}"):
            result = service.analyze_offline_html(project_id, upload.name, upload.getvalue())
            st.success(f"页面“{result['page']['title']}”提取 {len(result['elements'])} 个可交互元素。")
        package=st.file_uploader("上传离线站点 ZIP",type=["zip"],key=f"offline_zip_{project_id}")
        if package and st.button("安全解压并解析全部页面",key=f"parse_zip_{project_id}"):
            result=service.analyze_site_zip(project_id,package.name,package.getvalue()); st.session_state[f"site_result_{project_id}"]=result
        site=st.session_state.get(f"site_result_{project_id}")
        if site:
            entry=st.selectbox("入口页面",site["entry_candidates"],key=f"site_entry_{project_id}")
            st.caption(f"站点包 {site['site_package_id']}：{len(site['pages'])} 页，缺失资源 {len(site['missing_resources'])} 项")
            plan_text=st.text_area("需求驱动交互计划（JSON数组）",value='[{"action":"input","locator":"#name","value":"测试用户"}]',key=f"site_plan_{project_id}")
            if st.button("执行受限动态探索",key=f"explore_site_{project_id}"):
                import json
                st.json(service.explore_site_package(project_id,site["site_package_id"],entry,json.loads(plan_text)))
    rows = service.traceability_rows(project_id)
    names = ["需求层级", "原子需求", "HTML 元素", "需求—元素匹配", "覆盖矩阵", "用例版本"]
    for tab, key in zip(st.tabs(names), rows):
        with tab:
            st.dataframe(rows[key], use_container_width=True, hide_index=True)
    st.subheader("单条用例聊天式优化")
    cases=service.manager.list_generated_cases(project_id)
    if not cases:
        st.info("当前项目尚无已生成用例。")
        return
    by_id={x["case_id"]:x for x in cases}; case_id=st.selectbox("选择当前项目用例",list(by_id),key=f"regen_case_{project_id}")
    context=service.case_regeneration_context(project_id,case_id)
    with st.expander("用例、需求、原子需求、HTML证据和原始上下文",expanded=True): st.json(context)
    feedback=st.chat_input("输入仅针对该用例的修改反馈",key=f"regen_feedback_{project_id}_{case_id}")
    if feedback:
        with st.chat_message("user"): st.write(feedback)
        result=service.regenerate_single_case(project_id,case_id,feedback); st.session_state[f"regen_result_{project_id}_{case_id}"]=result
    result=st.session_state.get(f"regen_result_{project_id}_{case_id}")
    if result:
        st.write("字段级差异")
        changed=result["version"]["changed_fields"]
        st.dataframe([{"字段":key,"旧值":result["original"].get(key),"新值":result["revised"].get(key)} for key in changed],use_container_width=True)
        if result["coverage_gap_warning"]: st.warning("接受后将出现原子需求唯一覆盖缺口："+", ".join(result["coverage_gap_warning"]))
        version=int(result["version"]["version_no"]); a,b,c=st.columns(3)
        if a.button("接受新版本",key=f"accept_{project_id}_{case_id}_{version}"): service.update_case_version(project_id,case_id,version,"accept"); st.success("已接受，新版本已设为当前用例；旧版本仍保留。")
        if b.button("拒绝新版本",key=f"reject_{project_id}_{case_id}_{version}"): service.update_case_version(project_id,case_id,version,"reject"); st.info("已拒绝，当前用例未改变。")
        rollback=st.selectbox("回滚目标版本",[x["version_no"] for x in service.traceability_rows(project_id)["case_version_history"] if x["case_id"]==case_id],key=f"rollback_version_{project_id}_{case_id}")
        if c.button("回滚",key=f"rollback_{project_id}_{case_id}_{rollback}"): service.update_case_version(project_id,case_id,int(rollback),"rollback"); st.success("已创建并接受回滚版本，历史未删除。")
