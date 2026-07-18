"""Scenario-adapted project generation Agent page."""

import json

import streamlit as st

from core.six_quality_classifier import classify_requirement
from core.test_method_matcher import match_test_methods
from models.schemas import RequirementItem
from services.generation_service import GenerationRequest
from services.ui_state import begin_once, fail_once, finish_once, request_fingerprint

TEST_TYPES = [
    "功能测试",
    "性能测试",
    "接口测试",
    "异常测试",
    "安全性",
    "可靠性",
    "维修性",
    "保障性",
    "测试性",
    "环境适应性",
]


def _scenario_rows(cards):
    return [
        {
            "场景编号": c.get("scenario_id"),
            "场景名称": c.get("scenario_name"),
            "类型": c.get("scenario_type"),
            "关联需求": "、".join(c.get("related_requirements") or []),
            "参与者": "、".join(c.get("actors") or []),
            "触发事件": c.get("trigger_event"),
            "接口": "、".join(c.get("external_interfaces") or []),
            "来源片段": "、".join(c.get("source_chunk_ids") or []),
            "置信度": c.get("confidence"),
            "需确认": c.get("need_human_confirm"),
        }
        for c in cards
    ]


def _table_value(value):
    """Convert nested case fields to Arrow-friendly table values."""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _case_table_rows(cases):
    """Prepare generated cases for Streamlit dataframe display only."""
    return [{key: _table_value(value) for key, value in case.items()} for case in cases]


def render_generation_page(service, project_id, case_library, config) -> None:
    st.header("智能生成 · 场景适配 Agent")
    if not project_id:
        st.info("请先创建项目。")
        return
    c1, c2, c3 = st.columns(3)
    if c1.button("1. 抽取 / 更新项目画像"):
        text = service.combined_project_text(project_id)
        if not text:
            st.warning("没有可解析的项目文本。")
        else:
            service.extract_profile(project_id, config["use_ollama"])
            st.rerun()
    if c2.button("2. 抽取 / 更新需求点"):
        rows = service.extract_requirements(project_id)
        st.success(f"已抽取 {len(rows)} 条需求。")
        st.rerun()
    if c3.button("3. 抽取 / 更新场景卡片"):
        cards = service.extract_scenarios(project_id, config["use_ollama"])
        st.success(f"已抽取并保存 {len(cards)} 张场景卡片。")
        st.rerun()

    profile = service.get_profile(project_id)
    with st.expander("项目画像", expanded=False):
        st.json(profile or {"状态": "尚未抽取"})
    requirements = service.list_requirements(project_id)
    if not requirements:
        st.info("请先上传项目资料并抽取需求点。")
        return
    strategy = []
    for req in requirements:
        item = RequirementItem(
            requirement_id=req["requirement_id"],
            requirement_text=req.get("description", ""),
            test_object=(profile or {}).get("test_object", ""),
        )
        six = classify_requirement(item, False, None)
        strategy.append(
            {
                "需求编号": req["requirement_id"],
                "需求": req.get("description", ""),
                "六性匹配": "、".join(six),
                "测试方法": "、".join(match_test_methods(item, six)),
            }
        )
    st.subheader("需求点与测试策略")
    st.dataframe(strategy, hide_index=True, use_container_width=True)

    cards = service.list_scenario_cards(project_id)
    st.subheader("场景卡片")
    if cards:
        st.dataframe(_scenario_rows(cards), hide_index=True, use_container_width=True)
        with st.expander("查看场景卡完整内容"):
            st.json(cards)
    else:
        st.warning("尚未抽取场景卡片；仍可生成，但会标记需人工确认。")
    st.subheader("需求—场景关联")
    relation = []
    for req in requirements:
        related = [
            c
            for c in cards
            if req["requirement_id"] in (c.get("related_requirements") or [])
        ]
        relation.append(
            {
                "需求编号": req["requirement_id"],
                "需求摘要": req.get("title", ""),
                "关联场景": "、".join(c.get("scenario_name", "") for c in related)
                or "未关联",
                "场景数量": len(related),
            }
        )
    st.dataframe(relation, hide_index=True, use_container_width=True)

    st.subheader("场景化生成")
    labels = {
        f"{r['requirement_id']} - {r.get('title', '')}": r["requirement_id"]
        for r in requirements
    }
    selected_label = st.selectbox(
        "选择需求点", list(labels), key=f"scenario_req_{project_id}"
    )
    case_type = st.selectbox(
        "选择测试类型", TEST_TYPES, key=f"scenario_type_{project_id}"
    )
    use_kb = st.checkbox(
        "使用当前项目知识库", True, key=f"scenario_use_kb_{project_id}"
    )
    use_history = st.checkbox(
        "使用历史相似用例（仅参考写法）",
        config["use_library"],
        disabled=case_library is None,
        key=f"scenario_use_history_{project_id}",
    )
    req_id = labels[selected_label]
    p1, p2 = st.columns(2)
    if p1.button("预览生成上下文", key=f"preview_context_{project_id}"):
        st.session_state[f"advanced_context_{project_id}"] = service.preview_generation(
            project_id, req_id, case_type, int(config["top_k"]), use_kb, use_history
        )
    if p2.button(
        "一键生成场景化用例", type="primary", key=f"generate_scenario_case_{project_id}"
    ):
        with st.spinner("正在构造上下文、生成、审查并评分……"):
            payload = {
                "project_id": project_id,
                "requirement_id": req_id,
                "case_type": case_type,
                "use_kb": use_kb,
                "use_history": use_history,
            }
            fingerprint = request_fingerprint(payload)
            guard_key = f"generation:{project_id}"
            if not begin_once(st.session_state, guard_key, fingerprint):
                st.info("相同生成请求已完成或正在执行，未重复生成和写库。")
                return
            try:
                generated = service.generate(
                    GenerationRequest(
                        project_id=project_id,
                        requirement_ids=[req_id],
                        case_type=case_type,
                        use_project_kb=use_kb,
                        use_history=use_history,
                        requested_mode="auto" if config["use_ollama"] else "rule",
                    )
                )
                record = generated.cases[0]
                result = {
                    "case": record.persistence_data,
                    "quality": record.quality,
                    "generation_result": generated.model_dump(mode="json"),
                }
                finish_once(st.session_state, guard_key, fingerprint, result)
            except Exception as exc:
                fail_once(st.session_state, guard_key)
                st.error(f"生成失败：{type(exc).__name__}: {exc}")
                raise
            st.session_state[f"advanced_result_{project_id}"] = result
            st.success("场景化用例已生成、审查、评分并保存。")
    context = st.session_state.get(f"advanced_context_{project_id}")
    if context:
        with st.expander("生成前上下文预览", expanded=True):
            st.json(context)
    result = st.session_state.get(f"advanced_result_{project_id}")
    if result:
        metadata = result.get("generation_result") or {}
        sources = (
            metadata.get("retrieved_source_chunk_ids")
            or result["case"].get("source_chunk_ids")
            or []
        )
        st.caption(
            f"generation mode: {metadata.get('generation_mode', 'unknown')} · Agent 降级: {'是' if metadata.get('fallback_reason') else '否'} · 来源数量: {len(sources)}"
        )
        if metadata.get("fallback_reason"):
            st.warning(f"降级原因：{metadata['fallback_reason']}")
        missing = (
            metadata.get("overall_missing_information")
            or result["case"].get("missing_information")
            or []
        )
        if missing:
            st.warning("缺失信息：" + "；".join(missing))
        st.caption(
            f"需要人工确认：{'是' if result['case'].get('need_human_confirm') else '否'}"
        )
        st.subheader("最新场景化用例")
        st.json(result["case"])
        q1, q2 = st.columns([1, 3])
        q1.metric("质量评分", result["quality"]["score"])
        q2.dataframe(
            [result["quality"]["dimensions"]], hide_index=True, use_container_width=True
        )
        if result["quality"]["issues"]:
            st.warning("；".join(result["quality"]["issues"]))
        if result["quality"]["suggestions"]:
            st.info("；".join(result["quality"]["suggestions"]))

    st.subheader("已生成用例与质量")
    only_confirm = st.checkbox("只显示需人工确认", key=f"only_confirm_{project_id}")
    saved = [x.get("case_json") or {} for x in service.list_generated_cases(project_id)]
    if only_confirm:
        saved = [
            x
            for x in saved
            if x.get("need_human_confirm") or x.get("need_human_confirmation")
        ]
    if saved:
        st.dataframe(_case_table_rows(saved), hide_index=True, use_container_width=True)
    scores = service.list_quality_scores(project_id)
    if scores:
        with st.expander("历史质量评分"):
            st.dataframe(scores, hide_index=True, use_container_width=True)
    st.info("生成完成后，下一步请进入“结果审查与追溯”查看评分、来源并进行人工确认。")
