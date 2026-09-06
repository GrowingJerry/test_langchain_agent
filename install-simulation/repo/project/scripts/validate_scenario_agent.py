"""Minimal offline end-to-end validation for the scenario-adapted Agent."""

from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SAMPLE = """无人巡检车系统运行在园区无线网络环境。

操作员启动巡检任务时，系统应通过 REST API 接收包含路线编号的数据，并将状态由待命变为巡检中。

接口超时或路线编号无效时，系统应记录错误并保持安全停车状态。响应时间按需求文档规定值判定。
"""


def main() -> None:
    import application.services.project_service as project_manager_module
    from application.services.generation_context import ContextBuilder
    from application.services.generation_service import GenerationRequest, GenerationService
    from infrastructure.documents.ingestor import save_and_ingest_document
    from infrastructure.exporters.project_documents import export_project_excel, export_project_word
    from application.services.project_service import ProjectManager
    from workflows.learning.profile_extractor import extract_project_profile
    from workflows.learning.requirement_extractor import extract_requirements_from_chunks
    from workflows.scenario.card_extractor import extract_and_save_scenario_cards

    with tempfile.TemporaryDirectory(prefix="scenario-agent-") as temp:
        root = Path(temp)
        project_manager_module.PROJECT_OUTPUT_ROOT = root / "projects"
        manager = ProjectManager(root / "validation.db")
        conn = manager._connect()
        table_names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        scenario_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(scenario_cards)")
        }
        conn.close()
        assert {
            "scenario_cards",
            "case_generation_contexts",
            "case_quality_scores",
        } <= table_names
        assert {"scenario_name", "source_chunk_ids", "card_json"} <= scenario_columns
        project = manager.create_project(
            "无人巡检车最小验证项目", "场景 Agent 离线验证"
        )
        project_id = project["project_id"]
        save_and_ingest_document(
            manager, project_id, "项目说明.txt", SAMPLE.encode("utf-8")
        )
        text = manager.combined_project_text(project_id)
        manager.save_profile(
            project_id,
            extract_project_profile(text, project["project_name"], use_ollama=False),
        )
        requirements = extract_requirements_from_chunks(
            manager.list_chunks(project_id, 100)
        )
        manager.replace_requirements(project_id, requirements)
        cards = extract_and_save_scenario_cards(manager, project_id, use_ollama=False)
        assert requirements and cards, "需求或场景抽取为空"
        requirement_id = requirements[0]["requirement_id"]
        preview = ContextBuilder(manager).build(
            project_id, requirement_id, "接口测试", persist=False
        )
        generation = GenerationService(manager).generate_test_cases(
            GenerationRequest(
                project_id=project_id, requirement_ids=[requirement_id],
                case_type="接口测试", requested_mode="rule", use_history=False
            )
        )
        result = {
            "case": generation.cases[0].persistence_data,
            "quality": generation.cases[0].quality,
        }
        excel_path = export_project_excel(manager, project_id)
        word_path = export_project_word(manager, project_id)
        case = result["case"]
        combined = str(case)
        checks = {
            "项目场景": bool(case.get("related_scenario")),
            "接口": "REST API" in combined,
            "输入数据": bool(case.get("input_data")),
            "状态变化": "状态" in combined or "待命" in combined,
            "判定依据": bool(case.get("pass_criteria")),
            "来源追溯": bool(
                case.get("source_chunk_ids") and case.get("source_documents")
            ),
            "上下文已构造": bool(
                preview.get("related_chunks") and preview.get("related_scenario_cards")
            ),
            "质量评分已保存": bool(manager.list_quality_scores(project_id)),
            "Excel已导出": excel_path.is_file(),
            "Word已导出": word_path.is_file(),
        }
        assert all(checks.values()), checks
        print("scenario-agent-validation: OK")
        print("project_id:", project_id)
        print("requirement_id:", requirement_id)
        print("scenario_cards:", len(cards))
        print("quality_score:", result["quality"]["score"])
        print("checks:", checks)


if __name__ == "__main__":
    main()
