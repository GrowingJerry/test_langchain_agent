"""Integration tests for unified generation, fallback, persistence, and legacy facades."""

from __future__ import annotations

from pathlib import Path
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
from domain.exceptions import AgentExecutionError
from domain.schemas.test_case import TestCase
from application.services.generation_service import GenerationRequest, GenerationService


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


def test_invalid_agent_result_uses_rule_fallback(workspace) -> None:
    manager, project_id, _ = workspace
    result = GenerationService(
        manager,
        settings=enabled_settings(),
        health_client=Health(),
        agent_builder=lambda runtime: FakeAgent({"cases": [{"case_id": "broken"}]}),
        direct_model_generator=lambda contexts, request, reason: (_ for _ in ()).throw(RuntimeError("direct failed")),
    ).generate_test_cases(request(project_id))
    assert result.generation_mode == "rule_fallback"
    assert "ValidationError" in result.fallback_reason
    assert result.warnings


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
