"""Requirement-compatible and scenario-driven test-case generation page."""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

from application.services.generation_service import GenerationRequest
from application.services.test_type_recommendation import (
    apply_manual_case_type,
    sync_case_type_state,
)
from application.services.ui_state import request_fingerprint
from domain.rules.test_types import LABELS

TEST_TYPES = ["功能测试", "性能测试", "接口测试", "异常测试", "安全性测试", "可靠性测试"]


def _flat(value: Any) -> Any:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value


TEST_TYPES = LABELS


def _rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _flat(value) for key, value in item.items()} for item in items]


def _requirement_preview_rows(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in requirements:
        evidence = row.get("source_evidence") or []
        first = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
        rows.append({
            "原始需求编号": row.get("requirement_id", ""),
            "标题": row.get("title", ""),
            "章节路径": " / ".join(row.get("section_path") or []),
            "需求类型": row.get("requirement_type") or row.get("category", ""),
            "推荐测试类型": row.get("recommended_test_type", ""),
            "候选测试类型": row.get("alternative_test_types") or [],
            "置信度": row.get("test_type_confidence", 0),
            "判断依据": row.get("test_type_reasons") or [],
            "来源文档": ", ".join(row.get("source_documents") or [row.get("source_document", "")]),
            "位置": f"表{first.get('table_index') or ''} 行{first.get('row_index') or ''}".strip(),
            "需人工确认": bool(row.get("need_human_confirm")),
        })
    return _rows(rows)


def _review_rows(requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for row in requirements:
        rows.append({
            "retained": bool(row.get("retained", True)),
            "requirement_id": row.get("requirement_id", ""),
            "title": row.get("title", ""),
            "description": row.get("description", ""),
            "requirement_type": row.get("requirement_type", ""),
            "recommended_test_type": row.get("recommended_test_type", ""),
            "alternative_test_types": "、".join(row.get("alternative_test_types") or []),
            "inputs": "；".join(row.get("inputs") or []),
            "outputs": "；".join(row.get("outputs") or []),
            "exception_rules": "；".join(row.get("exception_rules") or []),
            "need_human_confirm": bool(row.get("need_human_confirm")),
        })
    return rows


def _rows_to_reviewed_dicts(edited: list[dict[str, Any]], machine: list[dict[str, Any]]) -> list[dict[str, Any]]:
    machine_by_id = {str(row.get("requirement_id") or ""): row for row in machine}
    result = []
    for row in edited:
        base = dict(machine_by_id.get(str(row.get("requirement_id") or ""), {}))
        base.update({
            "retained": bool(row.get("retained", True)),
            "requirement_id": row.get("requirement_id", ""),
            "title": row.get("title", ""),
            "description": row.get("description", ""),
            "requirement_type": row.get("requirement_type", ""),
            "recommended_test_type": row.get("recommended_test_type", ""),
            "alternative_test_types": [part.strip() for part in str(row.get("alternative_test_types") or "").replace(",", "、").split("、") if part.strip()],
            "inputs": [part.strip() for part in str(row.get("inputs") or "").split("；") if part.strip()],
            "outputs": [part.strip() for part in str(row.get("outputs") or "").split("；") if part.strip()],
            "exception_rules": [part.strip() for part in str(row.get("exception_rules") or "").split("；") if part.strip()],
            "need_human_confirm": bool(row.get("need_human_confirm")),
        })
        result.append(base)
    return result


def _execution_mode(config: dict[str, Any], key: str) -> str:
    selected = st.radio(
        "生成执行模式",
        ["Agent / 自动降级", "确定性规则"],
        horizontal=True,
        key=key,
        help="Agent失败时会显示真实原因，并使用完全相同的 GenerationPackage 切换到 Direct；Direct再失败则停止。",
    )
    return "auto" if selected.startswith("Agent") and config["use_ollama"] else "rule"


def _show_cases(payload: dict[str, Any], title: str) -> None:
    result = payload.get("generation_result") or payload
    st.subheader(title)
    if result.get("fallback_reason"):
        st.warning(f"已执行 rule_fallback：{result['fallback_reason']}")
    diagnostic_runs = result.get("diagnostic_runs") or []
    for run in diagnostic_runs:
        if run.get("agent_failure"):
            st.warning(
                f"Agent链生成失败：{run['agent_failure']}\n\n"
                "系统已使用相同需求、原子需求和 HTML 证据切换到 Direct 模式。"
            )
        st.info(
            f"需求 {run.get('requirement_id')} · 最终模式 {run.get('generation_mode')} · "
            f"上下文指纹一致：{run.get('agent_direct_context_equal')} · "
            f"运行 {run.get('diagnostic_run_id')} · 日志：{run.get('diagnostic_log_path')}"
        )
        bundle_path = run.get("diagnostic_bundle_path")
        if bundle_path:
            try:
                with open(bundle_path, "rb") as stream:
                    st.download_button(
                        "导出本次诊断包",
                        stream.read(),
                        file_name=bundle_path.replace("\\", "/").split("/")[-1],
                        mime="application/zip",
                        key=f"diagnostic_{run.get('diagnostic_run_id')}",
                    )
            except OSError:
                st.caption("诊断包暂不可读取，请查看日志路径。")
    missing = result.get("overall_missing_information") or []
    if missing:
        st.warning("缺失信息：" + "；".join(missing))
    cases = result.get("cases") or []
    if cases:
        st.dataframe(
            _rows([item.get("persistence_data") or item.get("case") or item for item in cases]),
            hide_index=True,
            use_container_width=True,
        )
        with st.expander("完整结构化结果"):
            st.json(result)


def _field_sources(scenario: dict[str, Any], provenance: dict[str, Any]) -> list[dict[str, Any]]:
    automatic: list[str] = []
    if provenance.get("source_chunk_ids"):
        automatic.append("项目文档")
    if provenance.get("knowledge_unit_ids"):
        automatic.append("approved知识")
    if provenance.get("template_id"):
        automatic.append("approved场景模板")
    inferred = "、".join(automatic) or "用户输入"
    return [
        {"字段": "场景目标", "自动值": scenario.get("scenario_goal"), "来源类型": "用户输入"},
        {"字段": "仿真对象", "自动值": scenario.get("simulation_object"), "来源类型": "用户输入"},
        {"字段": "任务阶段", "自动值": scenario.get("mission_phase"), "来源类型": "用户输入"},
        {"字段": "角色及功能", "自动值": scenario.get("role_requirements"), "来源类型": inferred},
        {"字段": "前置条件", "自动值": scenario.get("preconditions"), "来源类型": inferred},
        {"字段": "主流程", "自动值": scenario.get("normal_flow"), "来源类型": inferred},
        {"字段": "异常流程", "自动值": scenario.get("abnormal_flows"), "来源类型": inferred},
        {"字段": "恢复流程", "自动值": scenario.get("recovery_flow"), "来源类型": inferred},
        {"字段": "可观测变量", "自动值": scenario.get("observed_variables"), "来源类型": inferred},
    ]


def _show_allocations(scenario: dict[str, Any]) -> None:
    st.subheader("第四步：装备配置与数量依据")
    allocations = scenario.get("equipment_allocations") or []
    if not allocations:
        st.warning("未形成装备配置，相关角色需要人工确认。")
        return
    display = []
    for allocation in allocations:
        config = allocation.get("configuration") or {}
        quantity = allocation.get("quantity")
        rule_ids = allocation.get("rule_ids") or []
        refs = allocation.get("source_refs") or []
        display.append({
            "角色": allocation.get("role_requirement_id"),
            "装备ID": allocation.get("equipment_id"),
            "数量": quantity if quantity is not None else "待确认",
            "数量依据": config.get("formula_description") or "待确认",
            "规则ID": rule_ids or "待确认",
            "装备来源": "装备JSON" if any(ref.get("source_type") == "jsonl" for ref in refs) else "项目资料",
            "数量来源": "配置规则" if rule_ids else "用户确认",
            "需人工确认": bool(allocation.get("need_human_confirm") or quantity is None),
        })
    st.dataframe(_rows(display), hide_index=True, use_container_width=True)
    with st.expander("装备配置完整来源"):
        st.json(allocations)


def _difference_review(
    service: Any, project_id: str, scenario: dict[str, Any], validation: dict[str, Any]
) -> None:
    st.subheader("第五步：阻断问题和待确认项")
    scenario_id = scenario["scenario_id"]
    blockers = validation.get("blocking_issues") or []
    pending = list(dict.fromkeys([
        *(validation.get("warnings") or []),
        *(validation.get("missing_information") or []),
        *(scenario.get("missing_information") or []),
    ]))
    resolutions: dict[str, str] = {}
    if blockers:
        st.error("阻断问题必须逐项处理后才能批准场景。")
        for number, issue in enumerate(blockers, 1):
            st.write(f"阻断 {number}：{issue}")
            resolutions[issue] = st.text_input(
                "处理说明或人工确认依据", key=f"blocker_{project_id}_{scenario_id}_{number}"
            )
    else:
        st.success("当前没有阻断问题。")
    if pending:
        st.warning("待确认项：\n- " + "\n- ".join(pending))
    accepted_key = f"accepted_suggestions_{project_id}_{scenario_id}"
    st.session_state.setdefault(accepted_key, False)
    if st.button("接受全部非阻断建议", key=f"accept_suggestions_{project_id}_{scenario_id}"):
        st.session_state[accepted_key] = True
        st.success("已记录接受全部非阻断建议。")
    reviewer = st.text_input("审核人", key=f"reviewer_{project_id}_{scenario_id}")
    draft_col, approve_col = st.columns(2)
    review = dict(
        accept_non_blocking=st.session_state[accepted_key],
        blocking_resolutions=resolutions,
        reviewer=reviewer,
    )
    if draft_col.button("保存场景草稿", key=f"save_draft_{project_id}_{scenario_id}"):
        try:
            service.review_compiled_scenario(project_id, scenario_id, approve=False, **review)
            st.success("场景草稿和差异审核记录已保存。")
        except ValueError as exc:
            st.error(str(exc))
    if approve_col.button(
        "处理完成并批准场景", type="primary", key=f"approve_{project_id}_{scenario_id}"
    ):
        try:
            service.review_compiled_scenario(project_id, scenario_id, approve=True, **review)
            st.success("场景已批准。")
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def _approved_generation(service: Any, project_id: str, case_library: Any, config: dict[str, Any]) -> None:
    st.subheader("第六步：生成测试用例")
    approved = service.list_compiled_scenarios(project_id, "approved")
    if not approved:
        st.info("尚无已批准场景。完成差异审核后即可直接生成多条测试用例。")
        return
    scenario = st.selectbox(
        "已批准场景",
        approved,
        format_func=lambda row: f"{row.get('title')} · {row.get('scenario_category')}",
        key=f"approved_scenario_{project_id}",
    )
    left, right = st.columns(2)
    count = left.number_input("用例数量", 1, 20, 3, key=f"scenario_count_{project_id}")
    case_type = right.selectbox("测试类型", TEST_TYPES, key=f"scenario_type_{project_id}")
    mode = _execution_mode(config, f"scenario_exec_{project_id}")
    history = st.checkbox(
        "使用历史用例方法参考",
        value=bool(config["use_library"]),
        disabled=case_library is None,
        key=f"scenario_history_{project_id}",
    )
    if st.button("从批准场景生成多条用例", type="primary", key=f"scenario_generate_{project_id}"):
        try:
            with st.status("正在生成测试用例", expanded=True) as status:
                st.write("读取批准场景、装备配置和校验结果")
                generated = service.generate_from_approved_scenario(
                    project_id,
                    scenario["scenario_id"],
                    case_count=int(count),
                    case_type=case_type,
                    requested_mode=mode,
                    use_history=history,
                )
                st.write("执行受限 Agent 或 rule_fallback")
                st.session_state[f"scenario_result_{project_id}"] = generated.model_dump(mode="json")
                status.update(label="生成完成", state="complete")
        except Exception as exc:
            st.error(f"生成失败：{type(exc).__name__}: {exc}")
    if st.session_state.get(f"scenario_result_{project_id}"):
        _show_cases(st.session_state[f"scenario_result_{project_id}"], "场景驱动生成结果")


def _scenario_mode(service: Any, project_id: str, case_library: Any, config: dict[str, Any]) -> None:
    st.subheader("第一步：场景意图")
    with st.form(f"scenario_intent_{project_id}"):
        goal = st.text_area("想验证的目标")
        simulation_object = st.text_input("仿真对象或子系统")
        mission_phase = st.text_input("任务阶段")
        scale = st.number_input("场景规模", min_value=0, value=1)
        risks = st.text_area("重点风险或异常（每行或逗号分隔）")
        use_defaults = st.checkbox("使用项目默认配置", value=True)
        submitted = st.form_submit_button("自动场景编译", type="primary")
    if submitted:
        focus_risks = [
            value.strip() for value in risks.replace("，", ",").replace("\n", ",").split(",")
            if value.strip()
        ]
        bar = st.progress(0.0, text="准备场景编译")

        def progress(stage: str, value: float) -> None:
            bar.progress(value, text=stage)

        try:
            compiled = service.compile_scenario(project_id, {
                "scenario_goal": goal,
                "simulation_object": simulation_object,
                "target_subsystem": simulation_object,
                "mission_phase": mission_phase,
                "scale": int(scale),
                "focus_risks": focus_risks,
                "use_project_defaults": use_defaults,
                "additional_instructions": "",
                "title": goal[:80],
            }, progress_callback=progress)
            st.session_state[f"compiled_{project_id}"] = compiled
            st.success("场景编译完成，草稿已保存。")
        except Exception as exc:
            st.error(f"场景编译失败：{type(exc).__name__}: {exc}")
    compiled = st.session_state.get(f"compiled_{project_id}")
    if compiled:
        st.subheader("第二步：自动场景编译")
        st.caption(f"运行ID：{compiled['run_id']}；完成 {len(compiled['step_trace'])} 个工作流步骤")
        feedback_rule_ids = (compiled.get("provenance") or {}).get("feedback_rule_ids") or []
        with st.expander("为什么这次这样生成"):
            if feedback_rule_ids:
                st.json(service.explain_feedback_rules(
                    project_id, feedback_rule_ids,
                    allow_global=bool(compiled["intent"].get("use_project_defaults")),
                ))
            else:
                st.caption("本次没有命中已批准反馈规则；候选反馈不会参与生成。")
        scenario = st.selectbox(
            "第三步：展示场景草稿",
            compiled["scenarios"],
            format_func=lambda row: f"{row.get('title')} · {row.get('scenario_category')}",
            key=f"compiled_draft_{project_id}",
        )
        st.dataframe(_rows(_field_sources(scenario, compiled.get("provenance") or {})), hide_index=True)
        with st.expander("场景草稿完整内容"):
            st.json(scenario)
        _show_allocations(scenario)
        validation = next(
            row for row in compiled["validations"] if row["scenario_id"] == scenario["scenario_id"]
        )
        _difference_review(service, project_id, scenario, validation)
    _approved_generation(service, project_id, case_library, config)


def _requirement_mode(service: Any, project_id: str, case_library: Any, config: dict[str, Any]) -> None:
    st.caption("本页只负责生成；文档上传、结构增强和需求审核统一在“资料与需求”完成。")
    requirements = [
        row for row in service.list_requirements(project_id)
        if row.get("retained", True)
    ]
    if not requirements:
        st.info("尚无已确认需求。请先进入“资料与需求 → 3. 需求审核”。")
        return
    st.subheader("需求抽取预览")
    st.dataframe(_requirement_preview_rows(requirements), hide_index=True, use_container_width=True)
    select_all = st.checkbox(
        "全选全部需求", key=f"select_all_requirements_{project_id}",
        help="批量生成仍会逐条需求调用模型，并在每条需求完成后保存和记录断点。",
    )
    requirement_by_id = {str(row["requirement_id"]): row for row in requirements}
    requirement_ids = list(requirement_by_id)
    if select_all:
        selected_ids = requirement_ids
        st.caption(f"已选择全部 {len(selected_ids)} 条需求。")
    else:
        selected_ids = st.multiselect(
            "选择一个或多个需求",
            requirement_ids,
            default=requirement_ids[:1],
            format_func=lambda item: f"{item} · {requirement_by_id[item].get('title', '')}",
            key=f"selected_requirements_{project_id}",
        )
    if not selected_ids:
        st.warning("请至少选择一条需求。")
        return
    selected_requirements = [requirement_by_id[item] for item in selected_ids]
    recommendation = sync_case_type_state(
        st.session_state,
        selected_requirements,
        current_default=TEST_TYPES[0],
    )
    distribution = recommendation.get("type_distribution") or {}
    if distribution:
        st.caption("类型分布：" + "、".join(f"{key}{value}条" for key, value in distribution.items()))
    if recommendation.get("recommended_case_type"):
        st.info(
            f"系统推荐：{recommendation['recommended_case_type']}；"
            f"置信度：{float(recommendation.get('recommendation_confidence') or 0):.2f}"
        )
    else:
        st.warning("未能从需求中确定测试类型，请人工选择。")
    if recommendation.get("recommended_alternatives"):
        st.caption("候选类型：" + "、".join(recommendation["recommended_alternatives"]))
    if recommendation.get("recommendation_reasons"):
        with st.expander("推荐依据"):
            st.write("\n".join(f"- {item}" for item in recommendation["recommendation_reasons"]))
    if recommendation.get("needs_human_confirm"):
        st.warning("建议人工确认测试类型；用户最终选择会优先生效。")
    left, right = st.columns(2)
    case_type_key = f"requirement_type_{project_id}"
    if not recommendation.get("case_type_manually_overridden"):
        st.session_state[case_type_key] = recommendation["selected_case_type"]
    case_type = left.selectbox(
        "测试类型",
        TEST_TYPES,
        key=case_type_key,
    )
    recommendation = apply_manual_case_type(st.session_state, case_type)
    count_mode = right.radio(
        "用例数量方式", ["模型自动决定", "人工指定"], horizontal=True,
        key=f"requirement_count_mode_{project_id}",
        help="自动模式会让模型按需求复杂度覆盖正向和反向/异常路径，且避免重复。",
    )
    auto_case_count = count_mode == "模型自动决定"
    if auto_case_count:
        count = right.number_input(
            "自动生成上限", 2, 100, 20, key=f"requirement_auto_count_limit_{project_id}",
            help="这是安全上限，不是必须生成的数量。",
        )
    else:
        count = right.number_input(
            "用例数量", 1, 100, int(recommendation.get("selected_case_count") or 1),
            key=f"requirement_count_{project_id}",
        )
    recommendation["selected_case_count"] = int(count)
    if recommendation.get("case_type_manually_overridden"):
        st.warning(
            f"用户最终选择为 {recommendation['selected_case_type']}，与系统推荐 "
            f"{recommendation.get('recommended_case_type') or '未确定'} 不一致；本次生成将采用人工选择。"
        )
    st.info(
        "生成摘要："
        f"已选需求 {len(selected_ids)} 条；"
        f"系统推荐 {recommendation.get('recommended_case_type') or '未确定'}；"
        f"最终类型 {recommendation['selected_case_type']}；"
        + (f"用例数量由模型决定（最多 {int(count)} 条）。" if auto_case_count else f"用例数量 {int(count)}。")
    )
    mode = _execution_mode(config, f"requirement_exec_{project_id}")
    use_kb = st.checkbox("使用当前项目知识库", True, key=f"requirement_kb_{project_id}")
    history = st.checkbox(
        "使用历史用例方法参考", bool(config["use_library"]),
        disabled=case_library is None, key=f"requirement_history_{project_id}",
    )
    force_regenerate = st.checkbox(
        "重新生成本批次中已完成的需求",
        value=False,
        key=f"force_requirement_batch_{project_id}",
        help="默认关闭以支持断点续跑；打开后会忽略已有完成断点。",
    )
    manual_note = st.text_area(
        "人工备注要求（可选）",
        key=f"requirement_manual_note_{project_id}",
        placeholder="例如：重点覆盖断网恢复；禁止使用未在需求中出现的阈值。",
        help="备注会显示在生成上下文预览中，并作为本次生成的补充约束传给模型。",
    )
    preview_col, generate_col = st.columns(2)
    preview_state_key = f"generation_preview_context_{project_id}"
    if preview_col.button("预览生成上下文", key=f"preview_button_{project_id}"):
        preview_context = {
            requirement_id: service.preview_generation(
                project_id, requirement_id, recommendation["selected_case_type"],
                int(config["top_k"]), use_kb, history,
            )
            for requirement_id in selected_ids
        }
        st.session_state[preview_state_key] = {
            "人工备注要求": manual_note.strip() or "无",
            "项目生成上下文": preview_context,
        }
    if generate_col.button("按需求生成测试用例", type="primary", key=f"generate_{project_id}"):
        request_data = {
            "project_id": project_id, "requirement_ids": selected_ids,
            "case_type": case_type, "count": int(count), "mode": mode,
            "auto_case_count": auto_case_count, "manual_note": manual_note.strip(),
        }
        fingerprint = request_fingerprint(request_data)
        batch_id = f"REQ-BATCH-{fingerprint[:20]}"
        try:
            stream_area = st.empty()
            stream_text = {"reasoning": "", "token": ""}
            progress_bar = st.progress(0.0, text=f"准备批量生成，共 {len(selected_ids)} 条需求")

            def show_stream(event: dict[str, str]) -> None:
                kind = event.get("kind", "status")
                content = event.get("content", "")
                index = int(event.get("index") or 1)
                total = int(event.get("total") or len(selected_ids))
                requirement_id = event.get("requirement_id", "")
                progress_bar.progress(
                    min((index - 1) / max(total, 1), 0.99),
                    text=f"[{index}/{total}] {requirement_id}：{content[:80]}",
                )
                if kind in stream_text:
                    stream_text[kind] += content
                with stream_area.container():
                    with st.expander("模型生成过程（完成后自动收起）", expanded=True):
                        if stream_text["reasoning"]:
                            st.caption("思考过程")
                            st.code(stream_text["reasoning"][-6000:], language=None)
                        if stream_text["token"]:
                            st.caption("正在输出")
                            st.code(stream_text["token"][-6000:], language=None)
                        elif content:
                            st.write(content)

            batch_result = service.generate_requirement_batch(GenerationRequest(
                    project_id=project_id,
                    requirement_ids=selected_ids,
                    case_type=recommendation["selected_case_type"],
                    case_count=int(count),
                    auto_case_count=auto_case_count,
                    requested_mode=mode,
                    use_project_kb=use_kb,
                    use_history=history,
                    recommended_test_type=recommendation.get("recommended_case_type", ""),
                    selected_test_type=recommendation["selected_case_type"],
                    test_type_overridden=bool(recommendation.get("case_type_manually_overridden")),
                    test_type_confidence=float(recommendation.get("recommendation_confidence") or 0),
                    test_type_reasons=list(recommendation.get("recommendation_reasons") or []),
                    additional_instructions=manual_note.strip(),
            ), selected_ids, batch_id, progress_callback=show_stream, force=force_regenerate)
            st.session_state[f"requirement_result_{project_id}"] = batch_result
            progress_bar.progress(1.0, text="批量生成结束，已完成项均已持久化保存。")
            stream_area.empty()
            if batch_result["failed"]:
                st.warning(
                    f"批次结束：成功 {len(batch_result['completed'])}，"
                    f"断点跳过 {len(batch_result['skipped'])}，失败 {len(batch_result['failed'])}。"
                )
                st.json(batch_result["failed"])
            else:
                st.success(
                    f"批次完成：新生成 {len(batch_result['completed'])} 条需求，"
                    f"断点跳过 {len(batch_result['skipped'])} 条需求。"
                )
        except Exception as exc:
            st.error(f"批量生成启动失败：{type(exc).__name__}: {exc}")
    if st.session_state.get(preview_state_key):
        with st.expander("生成上下文预览"):
            st.json(st.session_state[preview_state_key])
    if st.session_state.get(f"requirement_result_{project_id}"):
        _show_cases(st.session_state[f"requirement_result_{project_id}"], "按需求生成结果")


def render_generation_page(
    service: Any, project_id: str, case_library: Any, config: dict[str, Any]
) -> None:
    st.header("生成测试用例")
    if not project_id:
        st.info("请先创建项目。")
        return
    st.caption("常规项目直接按已审核需求生成；场景编译属于可选的高级能力。")
    mode = st.radio(
        "生成方式", ["按需求生成", "高级场景生成"], horizontal=True,
        key=f"generation_mode_{project_id}",
    )
    if mode == "按需求生成":
        _requirement_mode(service, project_id, case_library, config)
    else:
        _scenario_mode(service, project_id, case_library, config)
    st.subheader("已生成用例")
    only_confirm = st.checkbox("只显示需人工确认", key=f"only_confirm_{project_id}")
    saved = [row.get("case_json") or {} for row in service.list_generated_cases(project_id)]
    if only_confirm:
        saved = [row for row in saved if row.get("need_human_confirm")]
    if saved:
        st.dataframe(_rows(saved), hide_index=True, use_container_width=True)
    scores = service.list_quality_scores(project_id)
    if scores:
        with st.expander("历史质量评分"):
            st.dataframe(scores, hide_index=True, use_container_width=True)
    st.info("生成后进入“用例审查”，可人工修改、单条对话优化并检查覆盖来源。")
