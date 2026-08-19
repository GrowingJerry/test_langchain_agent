"""Optional local-Ollama integration test; skipped unless explicitly enabled."""

import os
from pathlib import Path
import tempfile
import json

import pytest

from agents.test_case.agent import TestCaseAgent
from agents.test_case.context import AgentRuntimeContext
from agents.test_case.output_schema import TestCaseAgentRequest
from config.settings import settings
from infrastructure.documents.ingestor import save_and_ingest_document
import application.services.project_service as project_manager_module
from application.services.project_service import ProjectManager
from infrastructure.llm.ollama_health import OllamaHealthClient
from application.services.generation_context import ContextBuilder
from application.services.generation_package import build_generation_package
from application.services.generation_service import GenerationRequest, GenerationService
from domain.exceptions import AgentExecutionError


pytestmark = [
    pytest.mark.ollama,
    pytest.mark.skipif(
        os.getenv("RUN_OLLAMA_TESTS") != "1",
        reason="Set RUN_OLLAMA_TESTS=1 to run the local Ollama Agent integration test",
    ),
]


def test_agent_with_local_ollama() -> None:
    health = OllamaHealthClient(settings)
    if not health.is_available() or not health.model_exists(settings.ollama_model):
        pytest.skip("Configured Ollama service/model is unavailable")
    with tempfile.TemporaryDirectory(prefix="ollama-agent-integration-") as temp:
        root = Path(temp)
        project_manager_module.PROJECT_OUTPUT_ROOT = root / "projects"
        manager = ProjectManager(root / "workspace.db")
        project = manager.create_project("Agent 集成测试")
        project_id = project["project_id"]
        save_and_ingest_document(
            manager,
            project_id,
            "requirements.txt",
            "系统应接受订单数据。".encode("utf-8"),
        )
        chunk = manager.list_chunks(project_id, 1)[0]
        manager.save_profile(
            project_id, {"project_name": "Agent 集成测试", "test_object": "订单系统"}
        )
        manager.replace_requirements(
            project_id,
            [
                {
                    "requirement_id": "REQ-1",
                    "title": "订单接收",
                    "description": "系统应接受订单数据。",
                    "source_document": "requirements.txt",
                    "source_chunk_id": chunk["chunk_id"],
                }
            ],
        )
        with manager.connections.transaction() as conn:
            conn.execute("INSERT INTO requirement_indicators(project_id,indicator_id,capability_id,function_id,parent_indicator_id,indicator_text,indicator_type,source_json,rules_json,verification_scope,need_human_confirm) VALUES(?,?,?,?,?,?,?,?,?,?,0)",(project_id,'ATOM-ORDER','REQ-1','REQ-1','','接收有效订单数据','normal',json.dumps({'source_text':'系统应接受订单数据。'},ensure_ascii=False),'{}','offline_verifiable'))
            conn.execute("INSERT INTO html_pages(project_id,page_id,title,page_path,source_asset) VALUES(?,?,?,?,?)",(project_id,'PAGE-ORDER','订单录入','order.html','fixture.html'))
            conn.execute("INSERT INTO html_elements(project_id,element_id,page_id,tag,element_type,element_json) VALUES(?,?,?,?,?,?)",(project_id,'EL-SUBMIT','PAGE-ORDER','button','button',json.dumps({'element_id':'EL-SUBMIT','tag':'button','element_type':'button','text':'提交订单','label':'提交订单','region':'页面右下角操作区'},ensure_ascii=False)))
            conn.execute("INSERT INTO requirement_page_links(project_id,link_id,function_id,page_id,confidence,reason,status,need_human_confirm,model_name,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?)",(project_id,'PAGE-LINK','REQ-1','PAGE-ORDER',0.95,'订单接收需求对应订单录入页面','confirmed',0,settings.ollama_model,'{}'))
            conn.execute("INSERT INTO requirement_element_links(project_id,link_id,indicator_id,page_id,confirmed_element_id,candidates_json,confidence,reason,status,need_human_confirm) VALUES(?,?,?,?,?,?,?,?,?,0)",(project_id,'EL-LINK','ATOM-ORDER','PAGE-ORDER','EL-SUBMIT','["EL-SUBMIT"]',0.95,'提交订单控件','confirmed'))
            conn.execute("INSERT INTO html_observations(project_id,observation_id,page_id,action,result_json,evidence_json) VALUES(?,?,?,?,?,?)",(project_id,'OBS-ORDER','PAGE-ORDER','click',json.dumps({'visible_message':'订单已进入处理队列'},ensure_ascii=False),'{}'))
        runtime = AgentRuntimeContext(
            project_id=project_id, manager=manager, settings=settings
        )
        context = ContextBuilder(manager).build(
            project_id, "REQ-1", "功能测试", persist=False
        )
        package = build_generation_package(
            manager,
            context,
            num_ctx=settings.ollama_num_ctx,
            num_predict=settings.ollama_structured_num_predict,
        )
        assert package["package"]["atomic_requirements"]
        assert package["package"]["page_evidence"][0]["elements"][0]["element_id"] == "EL-SUBMIT"
        result = TestCaseAgent(runtime).generate(
            TestCaseAgentRequest(requirement_ids=["REQ-1"], case_count=1),
            generation_package=package["package"],
        )
        assert result.cases
        assert result.cases[0].structured_steps
        assert result.cases[0].structured_steps[0].element_id in {"", "EL-SUBMIT"}

        class ForcedFailureAgent:
            def generate(self, request, generation_package=None, progress_callback=None):
                raise AgentExecutionError("forced acceptance failure")

        direct_result = GenerationService(
            manager,
            settings=settings.model_copy(update={"enable_agent": True}),
            health_client=health,
            agent_builder=lambda runtime: ForcedFailureAgent(),
        ).generate_test_cases(GenerationRequest(
            project_id=project_id,
            requirement_ids=["REQ-1"],
            case_count=3,
            auto_case_count=True,
            requested_mode="auto",
            use_history=False,
        ))
        assert direct_result.generation_mode == "model_direct"
        assert direct_result.agent_direct_context_equal is True
        assert direct_result.context_fingerprints
        assert direct_result.cases
        assert all(item.case.structured_steps for item in direct_result.cases)
        assert any(
            step.element_id == "EL-SUBMIT"
            for item in direct_result.cases
            for step in item.case.structured_steps
        )
