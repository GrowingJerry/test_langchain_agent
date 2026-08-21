"""Integration tests for unified generation, fallback, persistence, and legacy facades."""

from __future__ import annotations

from pathlib import Path
import json
import tempfile
from typing import Any, List

import pytest

from agents.test_case.output_schema import GeneratedCaseBundle
from config.settings import Settings
import application.services.project_service as project_manager_module
from application.services.project_service import ProjectManager
from infrastructure.exporters.project_documents import (
    build_project_export_rows,
    export_project_excel,
    export_project_word,
)
from domain.exceptions import AgentExecutionError, StructuredOutputError
from domain.schemas.test_case import TestCase, StructuredExpectedResult, StructuredTestStep
from application.services.generation_service import GenerationRequest, GenerationService
from application.services.ui_application_service import UIApplicationService


class Health:
    def __init__(self, available: bool = True, model_exists: bool = True) -> None:
        self.available = available
        self.exists = model_exists

    def is_available(self) -> bool:
        return self.available

    def model_exists(self, model_name: str) -> bool:
        return self.exists


class FakeAgent:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error

    def generate(self, request) -> Any:
        if self.error is not None:
            raise self.error
        return self.result


class StreamingFakeAgent(FakeAgent):
    def generate(self, request, progress_callback=None) -> Any:
        if progress_callback:
            progress_callback({"kind": "reasoning", "content": "分析正反向路径"})
            progress_callback({"kind": "token", "content": "正在生成"})
        return super().generate(request)


def direct_case_generator(chunk_id: str):
    def generate(contexts: List[dict[str, Any]], request: GenerationRequest, reason: str) -> List[TestCase]:
        return [
            canonical_case(
                chunk_id,
                case_id="TC-DIRECT-1",
            ).model_copy(update={"generation_mode": "model_direct"})
        ]

    return generate


def canonical_case(
    chunk_id: str, scenario_id: str = "SCN-1", case_id: str = "TC-AGENT-1"
) -> TestCase:
    return TestCase(
        case_id=case_id,
        title="订单接收",
        objective="验证订单接收需求。",
        preconditions=["系统已启动"],
        test_steps=["提交需求规定的订单数据", "记录系统处理结果"],
        expected_results=["系统进入订单处理流程", "形成可追溯处理记录"],
        evaluation_criteria="按需求文档规定值判定",
        test_data=["需求规定的订单数据"],
        environment=[],
        requirement_ids=["REQ-1"],
        scenario_ids=[scenario_id],
        source_chunk_ids=[chunk_id],
        source_documents=["requirements.txt"],
        quality_category=["功能性"],
        test_method="功能验证测试",
        generation_mode="agent",
    )


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch):
    with tempfile.TemporaryDirectory(prefix="generation-service-") as temp:
        root = Path(temp)
        project_manager_module.PROJECT_OUTPUT_ROOT = root / "projects"
        manager = ProjectManager(root / "workspace.db")
        project = manager.create_project("订单系统")
        project_id = project["project_id"]
        upload = manager.uploads_dir(project_id) / "requirements.txt"
        upload.write_text("系统应接受订单数据并形成处理记录。", encoding="utf-8")
        document_id = manager.add_document(project_id, upload.name, "txt", upload)
        chunk_id = manager.replace_chunks(
            project_id,
            document_id,
            [
                {
                    "chunk_text": "系统应接受订单数据并形成处理记录。",
                    "source_type": "txt",
                }
            ],
        )[0]
        manager.save_profile(
            project_id, {"project_name": "订单系统", "test_object": "订单服务"}
        )
        manager.replace_requirements(
            project_id,
            [
                {
                    "requirement_id": "REQ-1",
                    "title": "订单接收",
                    "description": "系统应接受订单数据并形成处理记录。",
                    "category": "功能需求",
                    "source_document": "requirements.txt",
                    "source_chunk_id": chunk_id,
                }
            ],
        )
        manager.replace_full_scenario_cards(
            project_id,
            [
                {
                    "scenario_id": "SCN-1",
                    "project_id": project_id,
                    "scenario_name": "提交订单",
                    "related_requirements": ["REQ-1"],
                    "preconditions": ["系统已启动"],
                    "input_data": ["订单数据"],
                    "normal_flow": ["提交并处理"],
                    "source_document": ["requirements.txt"],
                    "source_chunk_ids": [chunk_id],
                    "need_human_confirm": False,
                }
            ],
        )
        chunk_rows = manager.list_chunks(project_id, 100)
        monkeypatch.setattr(
            "application.services.generation_context.search_project_chunks",
            lambda current_manager, current_project_id, query, top_k: list(chunk_rows),
        )
        yield manager, project_id, chunk_id


def enabled_settings(**updates: Any) -> Settings:
    values = {"enable_agent": True, "enable_ollama": True}
    values.update(updates)
    return Settings(**values)


def request(project_id: str, **updates: Any) -> GenerationRequest:
    values = {
        "project_id": project_id,
        "requirement_ids": ["REQ-1"],
        "scenario_ids": ["SCN-1"],
        "case_type": "功能测试",
        "case_count": 1,
    }
    values.update(updates)
    return GenerationRequest(**values)


def test_agent_generation_persists_run_case_trace_and_exports(workspace) -> None:
    manager, project_id, chunk_id = workspace
    bundle = GeneratedCaseBundle(
        cases=[canonical_case(chunk_id)],
        overall_missing_information=[],
        used_tool_names=["search_project_documents"],
        retrieved_source_chunk_ids=[chunk_id],
        warnings=[],
    )
    service = GenerationService(
        manager,
        settings=enabled_settings(),
        health_client=Health(),
        agent_builder=lambda runtime: FakeAgent(bundle),
    )
    result = service.generate_test_cases(request(project_id))
    assert result.generation_mode == "agent"
    assert result.fallback_reason == ""
    assert manager.list_generated_cases(project_id)
    assert manager.list_trace_sources(project_id)[0]["source_chunk_id"] == chunk_id
    assert manager.list_quality_scores(project_id)
    assert result.generation_run_id
    rows = build_project_export_rows(manager, project_id)
    assert rows["test_cases"][0]["case_name"] == "订单接收"
    assert export_project_excel(manager, project_id).is_file()
    assert export_project_word(manager, project_id).is_file()


def test_auto_count_notes_and_stream_callback_reach_agent(workspace) -> None:
    manager, project_id, chunk_id = workspace
    bundle = GeneratedCaseBundle(cases=[canonical_case(chunk_id)])
    events = []
    service = GenerationService(
        manager,
        settings=enabled_settings(ollama_structured_num_predict=32768, ollama_num_ctx=65536),
        health_client=Health(),
        agent_builder=lambda runtime: StreamingFakeAgent(bundle),
    )
    result = service.generate_test_cases(
        request(
            project_id,
            case_count=20,
            auto_case_count=True,
            additional_instructions="重点覆盖断网恢复",
        ),
        progress_callback=events.append,
    )
    assert len(result.cases) == 1
    assert {event["kind"] for event in events} >= {"status", "reasoning", "token"}
    with manager.connections.connection() as conn:
        run = conn.execute(
            "SELECT prompt_snapshot,metadata_json FROM generation_runs WHERE run_id=?",
            (result.generation_run_id,),
        ).fetchone()
    assert "重点覆盖断网恢复" in run["prompt_snapshot"]
    assert '"auto_case_count":true' in run["metadata_json"]


def test_requirement_batch_checkpoints_and_resumes_without_regeneration(workspace) -> None:
    manager, project_id, chunk_id = workspace
    ui = UIApplicationService.__new__(UIApplicationService)
    ui.manager = manager
    calls = []

    def generate_one(request, progress_callback=None):
        calls.append(request.requirement_ids[0])
        return GenerationService(
            manager,
            settings=enabled_settings(),
            health_client=Health(),
            agent_builder=lambda runtime: FakeAgent(
                GeneratedCaseBundle(cases=[canonical_case(
                    chunk_id, case_id=f"TC-{len(calls)}"
                )])
            ),
        ).generate_test_cases(request, progress_callback=progress_callback)

    ui.generate = generate_one
    base = request(project_id, scenario_ids=[], additional_instructions="统一批量备注")
    first = ui.generate_requirement_batch(base, ["REQ-1"], "BATCH-1")
    second = ui.generate_requirement_batch(base, ["REQ-1"], "BATCH-1")

    assert first["completed"] == ["REQ-1"]
    assert second["skipped"] == ["REQ-1"]
    assert calls == ["REQ-1"]
    assert manager.completed_batch_requirements(project_id, "BATCH-1") == ["REQ-1"]


def test_agent_disabled_uses_recorded_rule_fallback(workspace) -> None:
    manager, project_id, _ = workspace
    called: List[bool] = []
    service = GenerationService(
        manager,
        settings=enabled_settings(enable_agent=False),
        health_client=Health(),
        agent_builder=lambda runtime: called.append(True),
    )
    result = service.generate_test_cases(request(project_id))
    assert result.generation_mode == "rule_fallback"
    assert result.fallback_reason == "agent_disabled"
    assert not called
    assert result.cases[0].case.generation_mode == "rule_fallback"


def test_generation_request_uses_selected_type_and_persists_override_metadata(workspace) -> None:
    manager, project_id, _ = workspace
    service = GenerationService(
        manager,
        settings=enabled_settings(enable_agent=False),
        health_client=Health(),
    )
    result = service.generate_test_cases(
        request(
            project_id,
            case_type="性能测试",
            case_count=3,
            recommended_test_type="功能测试",
            selected_test_type="性能测试",
            test_type_overridden=True,
            test_type_confidence=0.91,
            test_type_reasons=["需求位于性能需求章节"],
        )
    )
    assert result.generation_mode == "rule_fallback"
    assert len(result.cases) == 3
    assert result.cases[0].persistence_data["case_type"] == "性能测试"
    assert "响应时间" in "\n".join(result.cases[0].case.test_steps)
    with manager.connections.connection() as conn:
        run = conn.execute(
            "SELECT metadata_json FROM generation_runs WHERE run_id=?",
            (result.generation_run_id,),
        ).fetchone()
        feedback = conn.execute(
            "SELECT COUNT(*) FROM feedback_candidates WHERE project_id=? AND candidate_type='test_type_override'",
            (project_id,),
        ).fetchone()[0]
    assert '"recommended_test_type":"功能测试"' in run["metadata_json"]
    assert '"selected_test_type":"性能测试"' in run["metadata_json"]
    assert '"case_count":3' in run["metadata_json"]
    assert feedback == 1


def test_legacy_requirement_without_recommendation_still_generates(workspace) -> None:
    manager, project_id, _ = workspace
    result = GenerationService(
        manager,
        settings=enabled_settings(enable_agent=False),
        health_client=Health(),
    ).generate_test_cases(request(project_id, case_type="功能测试"))
    assert result.cases
    assert result.cases[0].persistence_data["case_type"] == "功能测试"


def test_ollama_unavailable_uses_rule_fallback(workspace) -> None:
    manager, project_id, _ = workspace
    result = GenerationService(
        manager,
        settings=enabled_settings(),
        health_client=Health(available=False),
    ).generate_test_cases(request(project_id))
    assert result.generation_mode == "rule_fallback"
    assert result.fallback_reason == "ollama_unavailable"


def test_invalid_agent_and_direct_result_stops_without_fake_fallback(workspace) -> None:
    manager, project_id, _ = workspace
    with pytest.raises(StructuredOutputError, match="Agent 与 Direct 均失败"):
        GenerationService(
            manager,
            settings=enabled_settings(),
            health_client=Health(),
            agent_builder=lambda runtime: FakeAgent({"cases": [{"case_id": "broken"}]}),
            direct_model_generator=lambda contexts, request, reason: (_ for _ in ()).throw(RuntimeError("direct failed")),
        ).generate_test_cases(request(project_id))
    assert manager.list_generated_cases(project_id) == []


def test_agent_tool_failure_uses_direct_model_before_rule_fallback(workspace) -> None:
    manager, project_id, chunk_id = workspace
    result = GenerationService(
        manager,
        settings=enabled_settings(),
        health_client=Health(),
        agent_builder=lambda runtime: FakeAgent(
            error=AgentExecutionError("tool failed")
        ),
        direct_model_generator=direct_case_generator(chunk_id),
    ).generate_test_cases(request(project_id))
    assert result.generation_mode == "model_direct"
    assert result.fallback_reason == ""
    assert result.cases[0].case.generation_mode == "model_direct"
    assert "tool failed" in result.warnings[0]


def test_progress_persistence_failure_does_not_kill_direct_or_formal_persistence(workspace) -> None:
    manager, project_id, chunk_id = workspace

    def broken_task_state_callback(event):
        raise PermissionError(5, "state write denied")

    result = GenerationService(
        manager,
        settings=enabled_settings(),
        health_client=Health(),
        agent_builder=lambda runtime: FakeAgent(error=AgentExecutionError("agent unavailable")),
        direct_model_generator=direct_case_generator(chunk_id),
    ).generate_test_cases(request(project_id), progress_callback=broken_task_state_callback)

    assert result.generation_mode == "model_direct"
    assert result.cases
    assert manager.list_generated_cases(project_id)


def test_agent_and_direct_share_generation_package_fingerprint(workspace) -> None:
    manager, project_id, chunk_id = workspace
    captured: dict[str, Any] = {}

    class PackageAwareFailingAgent:
        def generate(self, request, generation_package=None, progress_callback=None):
            captured["agent_package"] = generation_package
            raise AgentExecutionError("forced chain failure")

    def direct(packages, request, reason):
        captured["direct_packages"] = packages
        return [canonical_case(chunk_id, case_id="TC-DIRECT-PACKAGE")]

    result = GenerationService(
        manager,
        settings=enabled_settings(),
        health_client=Health(),
        agent_builder=lambda runtime: PackageAwareFailingAgent(),
        direct_model_generator=direct,
    ).generate_test_cases(request(project_id))

    assert result.generation_mode == "model_direct"
    assert result.agent_direct_context_equal is True
    assert captured["agent_package"] == captured["direct_packages"][0]["package"]
    assert result.context_fingerprints == [captured["direct_packages"][0]["fingerprint"]]
    assert Path(result.diagnostic_log_path).is_file()
    assert Path(result.diagnostic_bundle_path).is_file()


def test_capacity_preflight_blocks_large_8b_request_before_model_call(workspace) -> None:
    manager, project_id, _ = workspace
    called = []
    service = GenerationService(
        manager,
        settings=enabled_settings(ollama_num_ctx=8192, ollama_structured_num_predict=2048),
        health_client=Health(),
        agent_builder=lambda runtime: called.append(True),
    )

    with pytest.raises(StructuredOutputError, match="generation_capacity_insufficient"):
        service.generate_test_cases(request(project_id, case_count=20, auto_case_count=True))

    assert called == []
    assert manager.list_generated_cases(project_id) == []


def test_auto_prefers_direct_when_generation_package_has_atomic_requirements(workspace) -> None:
    manager, project_id, chunk_id = workspace
    with manager.connections.transaction() as conn:
        conn.execute(
            "INSERT INTO requirement_indicators(project_id,indicator_id,capability_id,function_id,parent_indicator_id,indicator_text,indicator_type,source_json,rules_json,verification_scope,need_human_confirm) VALUES(?,?,?,?,?,?,?,?,?,?,0)",
            (project_id, "ATOM-1", "REQ-1", "REQ-1", "", "接收订单", "normal", "{}", "{}", "offline_verifiable"),
        )
    service = GenerationService(
        manager,
        settings=enabled_settings(),
        health_client=Health(),
        agent_builder=lambda runtime: (_ for _ in ()).throw(AssertionError("Agent must not run")),
        direct_model_generator=direct_case_generator(chunk_id),
    )

    result = service.generate_test_cases(request(project_id))

    assert result.generation_mode == "model_direct"
    assert result.agent_failure == ""
    assert result.agent_direct_context_equal is True


def test_direct_length_response_is_classified_output_truncated_without_retry(workspace, monkeypatch) -> None:
    manager, project_id, _ = workspace
    calls = []

    class Response:
        ok = True
        status_code = 200
        def iter_lines(self, decode_unicode=True):
            yield json.dumps({"message":{"content":"{"}, "done":True, "done_reason":"length", "eval_count":2048, "prompt_eval_count":4669})
        def close(self): pass

    monkeypatch.setattr("application.services.generation_service.requests.post", lambda *a, **k: calls.append(k) or Response())
    service = GenerationService(manager, settings=enabled_settings(), health_client=Health())

    with pytest.raises(StructuredOutputError, match="output_truncated"):
        service._generate_with_direct_model(
            [{"package":{"requirement":{"requirement_id":"REQ-1"}}, "fingerprint":"FP"}],
            request(project_id), "forced", progress_callback=lambda event: None,
        )

    assert len(calls) == 1


def test_direct_model_repairs_unpaired_step_result_without_rule_fallback(workspace) -> None:
    manager, project_id, chunk_id = workspace

    def unpaired_direct_generator(contexts, request, reason):
        return [
            canonical_case(chunk_id, case_id="TC-DIRECT-UNPAIRED").model_copy(
                update={
                    "generation_mode": "model_direct",
                    "test_steps": ["step one", "step two"],
                    "expected_results": ["result one"],
                }
            )
        ]

    result = GenerationService(
        manager,
        settings=enabled_settings(),
        health_client=Health(),
        agent_builder=lambda runtime: FakeAgent(
            error=AgentExecutionError("tool failed")
        ),
        direct_model_generator=unpaired_direct_generator,
    ).generate_test_cases(request(project_id))

    case = result.cases[0].case
    assert result.generation_mode == "model_direct"
    assert result.fallback_reason == ""
    assert len(case.test_steps) == 1
    assert len(case.expected_results) == 1
    assert case.need_human_confirm is True
    assert "未配对的测试步骤" in "\n".join(case.missing_information)


def test_direct_model_analysis_json_is_converted_to_reviewable_case(workspace) -> None:
    manager, project_id, chunk_id = workspace
    service = GenerationService(
        manager,
        settings=enabled_settings(),
        health_client=Health(),
    )
    contexts = service._build_contexts(request(project_id))
    parsed = {
        "用户画像": {"行业": "国防/军事"},
        "关联场景卡片": [{"场景名称": "系统性能验证"}],
        "明确测试对象": ["数据采集软件", "安全审计模块"],
        "场景输入数据": ["传感器原始数据", "日志数据"],
        "性能判定阈值": {"响应时间": "≤2s", "可用度": "≥99.9%"},
    }

    cases = service._prepare_model_direct_cases(
        service._coerce_direct_model_json_to_cases(
            parsed,
            contexts,
            request(project_id, case_type="性能测试"),
        ),
        request(project_id, case_type="性能测试"),
    )

    assert cases[0].generation_mode == "model_direct"
    assert cases[0].need_human_confirm is True
    assert cases[0].test_method == "性能测试"
    assert cases[0].source_chunk_ids == [chunk_id]
    assert "Direct model returned analysis JSON" in "\n".join(cases[0].missing_information)


def test_repeated_request_does_not_overwrite_primary_key(workspace) -> None:
    manager, project_id, _ = workspace
    service = GenerationService(
        manager,
        settings=enabled_settings(enable_agent=False),
        health_client=Health(),
    )
    first = service.generate_test_cases(request(project_id))
    second = service.generate_test_cases(request(project_id))
    assert first.cases[0].case.case_id != second.cases[0].case.case_id
    assert len(manager.list_generated_cases(project_id)) == 2


def _quality_candidate(chunk_id: str, index: int, *, action: str = "observe", input_value: str = "", element_id: str = "") -> TestCase:
    return TestCase(
        case_id=f"TC-MODEL-{index:03d}", title=f"候选{index}", objective="验证订单接收",
        preconditions=["订单服务已启动"], test_steps=["执行并观察订单接收"],
        expected_results=["形成可观察的订单处理记录"], evaluation_criteria="处理记录与需求规定一致",
        test_data=[], requirement_ids=["REQ-1"], source_chunk_ids=[chunk_id],
        structured_steps=[StructuredTestStep(
            step_no=1, action=action, element_id=element_id, input_value=input_value,
            instruction="执行并观察订单接收", expected_result=StructuredExpectedResult(data_change="形成订单处理记录")
        )],
    )


def test_click_does_not_require_input_value_and_missing_input_becomes_draft(workspace) -> None:
    manager, project_id, chunk_id = workspace
    click = _quality_candidate(chunk_id, 1, action="click")
    missing = _quality_candidate(chunk_id, 2, action="input")
    service = GenerationService(manager, settings=enabled_settings(ollama_num_ctx=32768,ollama_structured_num_predict=8192), health_client=Health(),
        agent_builder=lambda runtime: FakeAgent(GeneratedCaseBundle(cases=[click, missing])))
    result = service.generate_test_cases(request(project_id, case_count=2, scenario_ids=[]))
    assert len(result.cases) == 2 and result.rejected_case_count == 0
    click_record = next(row for row in result.cases if row.case.title == "候选1")
    missing_record = next(row for row in result.cases if row.case.title == "候选2")
    assert not any(item.get("code") == "missing_concrete_input" for item in click_record.case.quality_issues)
    assert missing_record.review_status == "draft_needs_review"
    assert any(item.get("code") == "missing_concrete_input" for item in missing_record.case.quality_issues)
    assert all(not row.case.case_id.startswith(("TC-MODEL", "case-")) for row in result.cases)


def test_input_value_is_repaired_from_test_data(workspace) -> None:
    manager, project_id, chunk_id = workspace
    case = _quality_candidate(chunk_id, 1, action="input").model_copy(update={"test_data":["手机号：13800138000"]})
    service = GenerationService(manager, settings=enabled_settings(ollama_num_ctx=32768,ollama_structured_num_predict=8192), health_client=Health(),
        agent_builder=lambda runtime: FakeAgent(GeneratedCaseBundle(cases=[case])))
    result = service.generate_test_cases(request(project_id, scenario_ids=[]))
    assert result.cases[0].case.structured_steps[0].input_value == "13800138000"
    assert any(item.get("code") == "input_value_repaired" for item in result.cases[0].case.quality_issues)


def test_one_hard_error_does_not_discard_valid_sibling(workspace) -> None:
    manager, project_id, chunk_id = workspace
    valid = _quality_candidate(chunk_id, 1)
    fabricated = _quality_candidate(chunk_id, 2, action="click", element_id="EL-NOT-EXISTS")
    service = GenerationService(manager, settings=enabled_settings(ollama_num_ctx=32768,ollama_structured_num_predict=8192), health_client=Health(),
        agent_builder=lambda runtime: FakeAgent(GeneratedCaseBundle(cases=[valid, fabricated])))
    result = service.generate_test_cases(request(project_id, case_count=2, scenario_ids=[]))
    assert len(result.cases) == 1 and result.rejected_case_count == 1
    assert "EL-NOT-EXISTS" in result.rejected_cases[0]["reason"]
    assert result.rejected_cases[0]["candidate_label"].startswith("候选用例2《")
    assert "TC-MODEL" not in result.rejected_cases[0]["candidate_label"]
    assert len(manager.list_generated_cases(project_id)) == 1
    assert Path(result.diagnostic_log_path).parent.joinpath("05-hard-rejected-cases.json").exists()


def test_eight_cases_preserve_seven_ready_and_one_review_draft(workspace) -> None:
    manager, project_id, chunk_id = workspace
    cases = [_quality_candidate(chunk_id,index,action="input" if index == 8 else "observe") for index in range(1,9)]
    service = GenerationService(manager, settings=enabled_settings(ollama_num_ctx=65536,ollama_structured_num_predict=16384), health_client=Health(),
        agent_builder=lambda runtime: FakeAgent(GeneratedCaseBundle(cases=cases)))
    result = service.generate_test_cases(request(project_id,case_count=8,scenario_ids=[]))
    assert result.valid_case_count == 7
    assert result.review_case_count == 1
    assert result.rejected_case_count == 0
    assert len(manager.list_generated_cases(project_id)) == 8
    with manager.connections.connection() as conn:
        statuses={row[0] for row in conn.execute("SELECT DISTINCT coverage_status FROM case_indicator_links WHERE project_id=?",(project_id,))}
    assert statuses <= {"confirmed","proposed"}


def test_batch_continues_after_review_draft_and_completes_next_requirement(workspace) -> None:
    manager, project_id, chunk_id = workspace
    manager.upsert_requirement(project_id,{"requirement_id":"REQ-2","title":"查询订单","description":"查询并观察订单记录","source_document":"requirements.txt","source_chunk_id":chunk_id})
    ui = UIApplicationService.__new__(UIApplicationService); ui.manager=manager
    def generate_one(current_request, progress_callback=None):
        requirement_id=current_request.requirement_ids[0]
        case=_quality_candidate(chunk_id,1,action="input" if requirement_id=="REQ-1" else "observe").model_copy(update={"requirement_ids":[requirement_id],"title":requirement_id})
        return GenerationService(manager,settings=enabled_settings(ollama_num_ctx=32768,ollama_structured_num_predict=8192),health_client=Health(),
            agent_builder=lambda runtime:FakeAgent(GeneratedCaseBundle(cases=[case]))).generate_test_cases(current_request,progress_callback=progress_callback)
    ui.generate=generate_one
    summary=ui.generate_requirement_batch(request(project_id,scenario_ids=[]),["REQ-1","REQ-2"],"QUALITY-BATCH")
    assert summary["completed"] == ["REQ-1","REQ-2"]
    assert summary["failed"] == []
    assert summary["valid_case_count"] == 1 and summary["review_case_count"] == 1
    assert len(summary["requirements"]) == 2


def test_draft_accept_and_inactive_update_coverage_status(workspace) -> None:
    manager, project_id, _ = workspace
    case={"case_id":"REQ-1-ORDER-0099","requirement_id":"REQ-1","case_type":"功能测试",
          "test_steps":["执行"],"indicator_ids":["ATOM-QUALITY"],"review_status":"draft_needs_review",
          "need_human_confirm":True,"quality_issues":[{"code":"missing_concrete_input","severity":"review_required","message":"缺少具体输入"}]}
    manager.save_generated_case(project_id,case)
    ui=UIApplicationService.__new__(UIApplicationService); ui.manager=manager
    with manager.connections.connection() as conn:
        assert conn.execute("SELECT coverage_status FROM case_indicator_links WHERE project_id=? AND case_id=?",(project_id,case["case_id"])).fetchone()[0] == "proposed"
    ui.set_case_review_status(project_id,case["case_id"],"accepted")
    with manager.connections.connection() as conn:
        assert conn.execute("SELECT coverage_status FROM case_indicator_links WHERE project_id=? AND case_id=?",(project_id,case["case_id"])).fetchone()[0] == "confirmed"
    ui.set_case_review_status(project_id,case["case_id"],"inactive")
    with manager.connections.connection() as conn:
        assert conn.execute("SELECT coverage_status FROM case_indicator_links WHERE project_id=? AND case_id=?",(project_id,case["case_id"])).fetchone()[0] == "inactive"
