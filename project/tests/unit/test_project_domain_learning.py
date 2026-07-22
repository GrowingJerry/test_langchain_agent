from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableLambda

import application.services.project_service as manager_module
from chains.knowledge_extraction import KnowledgeExtractionChain
from config.settings import Settings
from application.services.project_service import ProjectManager
from workflows.learning.learning_task_service import LearningTaskService


class StructuredModel:
    def __init__(self, output: dict[str, Any]) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    def with_structured_output(self, schema: Any) -> RunnableLambda:
        def invoke(value: Any) -> dict[str, Any]:
            self.calls.append({"schema": schema, "value": value})
            return self.output

        return RunnableLambda(invoke)


@pytest.fixture
def simulated_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    manager = ProjectManager(tmp_path / "workflows.learning.db")
    project_id = manager.create_project("flight simulation")["project_id"]
    path = manager.uploads_dir(project_id) / "technical-book.pdf"
    path.write_bytes(b"book")
    document_id = manager.add_document(project_id, path.name, "pdf", path, "book-hash", "pymupdf")
    chunks = []
    for page in range(1, 201):
        section = "SEC-DYNAMIC" if page <= 100 else "SEC-FAULT"
        text = (
            f"飞行器动力学模型 状态变量 高度 h 参数示例 100 page {page}"
            if page == 12
            else f"常规技术资料 page {page}"
        )
        if page == 140:
            text = "故障模式下高度符号 h 表示传感器输出"
        chunks.append({
            "chunk_text": text, "page_no": page, "page_start": page,
            "page_end": page, "chunk_level": "child", "parent_section_id": section,
            "section_title": "动力学" if page <= 100 else "故障模式",
        })
    manager.replace_chunks(project_id, document_id, chunks)
    return manager, project_id, document_id


def _task(service: LearningTaskService, project_id: str, document_id: str):
    return service.create_task(
        project_id, task_name="领域学习", learning_goal="学习飞行器状态与故障",
        domain="飞行仿真", simulation_object="被测飞行器",
        target_subsystems=["动力学"], target_topics=["状态变量", "故障模式"],
        expected_scenario_types=["故障场景"], excluded_topics=["历史沿革"],
        selected_document_ids=[document_id],
    )


def test_two_stage_learning_only_extracts_relevant_candidates(simulated_book) -> None:
    manager, project_id, document_id = simulated_book
    model = StructuredModel({"knowledge_units": [{
        "knowledge_type": "state_variable", "title": "高度", "content": "高度是状态变量",
        "normalized_data": {"symbol": "h"}, "tags": ["动力学"],
        "applicable_conditions": ["飞行状态"], "inapplicable_conditions": ["传感器故障"],
        "source_chunk_ids": [], "source_page": 12, "confidence": 0.9,
    }]})
    service = LearningTaskService(
        manager, KnowledgeExtractionChain(Settings(enable_ollama=False), model=model)
    )
    task = _task(service, project_id, document_id)
    candidates = service.extractor.retrieve_candidates(task)
    assert len(candidates) < 200
    assert {item.page_start for item in candidates} == {12, 140}
    model.output["knowledge_units"][0]["source_chunk_ids"] = [candidates[0].chunk_id]
    result = service.run_task(project_id, task.task_id)
    assert result["status"] == "completed"
    assert len(model.calls) == 1
    assert result["knowledge_units"][0]["document_id"] == document_id
    assert result["knowledge_units"][0]["applicable_conditions"]
    assert result["report"]["document_coverage"][0]["covered"] is True
    assert "state_variable" in result["report"]["knowledge_type_coverage"]


def test_parameter_without_unit_and_generic_project_claim_needs_review(simulated_book) -> None:
    manager, project_id, document_id = simulated_book
    seed = LearningTaskService(manager, KnowledgeExtractionChain(Settings(enable_ollama=False), model=StructuredModel({"knowledge_units": []})))
    task = _task(seed, project_id, document_id)
    candidates = seed.extractor.retrieve_candidates(task)
    model = StructuredModel({"knowledge_units": [{
        "knowledge_type": "parameter", "title": "高度示例值", "content": "高度取100",
        "normalized_data": {}, "parameter": {"name": "高度", "symbol": "h", "value": 100,
        "unit": "", "valid_range": "", "operating_condition": "示例",
        "related_model": "动力学", "value_type": "项目值", "source_page": 12},
        "tags": [], "applicable_conditions": ["示例"], "inapplicable_conditions": ["项目定型"],
        "source_chunk_ids": [candidates[0].chunk_id], "source_page": 12, "confidence": 0.99,
    }]})
    service = LearningTaskService(manager, KnowledgeExtractionChain(Settings(enable_ollama=False), model=model))
    result = service.run_task(project_id, task.task_id)
    unit = result["knowledge_units"][0]
    assert unit["status"] == "draft"
    assert unit["need_human_confirm"] is True
    assert unit["normalized_data"]["project_specific"] is False
    assert result["report"]["pending_review_count"] == 1


def test_empty_structured_output_is_recorded_as_failure(simulated_book) -> None:
    manager, project_id, document_id = simulated_book
    model = StructuredModel({"knowledge_units": []})
    service = LearningTaskService(manager, KnowledgeExtractionChain(Settings(enable_ollama=False), model=model))
    task = _task(service, project_id, document_id)
    result = service.run_task(project_id, task.task_id)
    assert result["status"] == "failed"
    assert result["errors"]
    stored = service.get_task(project_id, task.task_id)
    assert stored is not None and stored.status == "failed"
    assert stored.error_messages


def test_invalid_unit_is_reported_without_discarding_valid_units(simulated_book) -> None:
    manager, project_id, document_id = simulated_book
    seed = LearningTaskService(
        manager,
        KnowledgeExtractionChain(
            Settings(enable_ollama=False), model=StructuredModel({"knowledge_units": []})
        ),
    )
    task = _task(seed, project_id, document_id)
    candidate = seed.extractor.retrieve_candidates(task)[0]
    model = StructuredModel({"knowledge_units": [
        {
            "knowledge_type": "parameter", "title": "坏参数", "content": "格式规则",
            "source_chunk_ids": [candidate.chunk_id], "confidence": 0.9,
        },
        {
            "knowledge_type": "verification_rule", "title": "有效规则",
            "content": "每步对应一个预期结果", "source_chunk_ids": [candidate.chunk_id],
            "applicable_conditions": ["测试设计"], "inapplicable_conditions": [],
            "confidence": 0.9,
        },
    ]})
    service = LearningTaskService(
        manager, KnowledgeExtractionChain(Settings(enable_ollama=False), model=model)
    )
    result = service.run_task(project_id, task.task_id)
    assert result["status"] == "completed"
    assert [unit["title"] for unit in result["knowledge_units"]] == [
        "坏参数", "有效规则"
    ]
    assert result["knowledge_units"][0]["knowledge_type"] == "constraint"
    assert "坏参数" in result["errors"][0]
    assert "已降级为constraint候选" in result["errors"][0]
    assert result["report"]["extraction_errors"] == result["errors"]
