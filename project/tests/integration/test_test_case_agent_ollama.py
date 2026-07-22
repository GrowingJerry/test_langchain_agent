"""Optional local-Ollama integration test; skipped unless explicitly enabled."""

import os
from pathlib import Path
import tempfile

import pytest

from agents.test_case.agent import TestCaseAgent
from agents.test_case.context import AgentRuntimeContext
from agents.test_case.output_schema import TestCaseAgentRequest
from config.settings import settings
from infrastructure.documents.ingestor import save_and_ingest_document
import application.services.project_service as project_manager_module
from application.services.project_service import ProjectManager
from infrastructure.llm.ollama_health import OllamaHealthClient


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
        runtime = AgentRuntimeContext(
            project_id=project_id, manager=manager, settings=settings
        )
        result = TestCaseAgent(runtime).generate(
            TestCaseAgentRequest(requirement_ids=["REQ-1"], case_count=1)
        )
        assert result.cases
