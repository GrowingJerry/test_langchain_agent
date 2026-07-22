"""Exercise the real local-data pipeline with a live Ollama model."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chains.knowledge_extraction import KnowledgeExtractionChain  # noqa: E402
from config.settings import Settings  # noqa: E402
from core.project_document_exporter import export_project_excel, export_project_word  # noqa: E402
from core.project_manager import ProjectManager  # noqa: E402
from equipment.jsonl_importer import MilitaryJsonlImporter  # noqa: E402
from learning.document_job_runner import DocumentJobRunner  # noqa: E402
from learning.knowledge_review_service import KnowledgeReviewService  # noqa: E402
from learning.learning_task_service import LearningTaskService  # noqa: E402
from infrastructure.db.json_codec import loads_json  # noqa: E402
from scenario_engine.scenario_workflow import ScenarioWorkflow  # noqa: E402
from services.generation_service import GenerationRequest, GenerationService  # noqa: E402
from services.upload_service import UploadService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--report", type=Path, default=ROOT / "outputs/real_ollama_audit/report.json")
    args = parser.parse_args()
    report: Dict[str, Any] = {
        "started_at": datetime.now().isoformat(), "model": args.model,
        "stages": {}, "issues": [],
    }

    def stage(name: str, operation: Callable[[], Any]) -> Any:
        try:
            value = operation()
            report["stages"][name] = {"ok": True, "result": value}
            print(f"[OK] {name}")
            return value
        except Exception as exc:
            issue = {"stage": name, "error": f"{type(exc).__name__}: {exc}"}
            report["stages"][name] = {"ok": False, **issue}
            report["issues"].append(issue)
            print(f"[FAIL] {name}: {issue['error']}")
            return None

    settings = Settings(
        ollama_model=args.model,
        ollama_extraction_model=args.model,
        ollama_review_model=args.model,
        enable_ollama=True,
        enable_agent=True,
        upload_max_bytes=100 * 1024 * 1024,
        ollama_timeout=300,
        ollama_num_ctx=16384,
        ollama_structured_num_predict=2048,
    )
    pdf_path = next(ROOT.joinpath("data").glob("*.pdf"))
    docx_path = next(ROOT.joinpath("data").glob("*.docx"))
    jsonl_path = ROOT / "data/military.jsonl"

    with tempfile.TemporaryDirectory(prefix="real-ollama-audit-") as temp:
        manager = ProjectManager(Path(temp) / "audit.db")
        project_id = manager.create_project("真实Ollama全流程审查")["project_id"]
        upload = UploadService(manager, settings)

        equipment_report = stage(
            "equipment_jsonl_import",
            lambda: MilitaryJsonlImporter(manager.equipment).import_file(
                jsonl_path, project_id
            ).model_dump(mode="json"),
        )

        pdf_upload = stage(
            "pdf_enqueue",
            lambda: upload.ingest_document(
                project_id, pdf_path.name, pdf_path.read_bytes()
            ),
        )
        if pdf_upload and pdf_upload.get("job_id"):
            worker_result = stage(
                "pdf_worker",
                lambda: DocumentJobRunner(manager).run_once("real-audit-worker"),
            )
            if worker_result:
                pdf_document = next(
                    row for row in manager.list_documents(project_id)
                    if row["document_id"] == pdf_upload["document_id"]
                )
                report["stages"]["pdf_worker"]["document"] = {
                    "document_id": pdf_document["document_id"],
                    "filename": pdf_document["filename"],
                    "parser_type": pdf_document.get("parser_type"),
                    "processing_status": pdf_document.get("processing_status"),
                    "last_processed_page": pdf_document.get("last_processed_page"),
                    "parse_report": loads_json(pdf_document.get("parse_report_json"), {}),
                }

        docx_upload = stage(
            "text_document_ingestion",
            lambda: upload.ingest_document(project_id, docx_path.name, docx_path.read_bytes()),
        )
        learning_result = None
        if docx_upload and pdf_upload:
            learning = LearningTaskService(manager, KnowledgeExtractionChain(settings))
            task = stage(
                "learning_task_create",
                lambda: learning.create_task(
                    project_id, task_name="潜艇作战建模与仿真领域学习",
                    learning_goal=(
                        "学习潜艇遭受主动声自导鱼雷攻击时使用声诱饵、高频噪声干扰器"
                        "和规避机动的防御流程，并遵守测试步骤和预期结果编写规范"
                    ),
                    domain="潜艇作战建模与仿真", simulation_object="潜艇水下航行仿真系统",
                    target_subsystems=["潜艇防御鱼雷模型", "水声对抗系统"],
                    target_topics=["主动声自导鱼雷", "声诱饵", "高频噪声干扰器", "规避机动", "航向"],
                    expected_scenario_types=["nominal", "abnormal", "degraded", "recovery"],
                    excluded_topics=[], selected_document_ids=[
                        pdf_upload["document_id"], docx_upload["document_id"]
                    ],
                ).model_dump(mode="json"),
            )
            if task:
                learning_result = stage(
                    "ollama_knowledge_extraction",
                    lambda: learning.run_task(project_id, task["task_id"]),
                )
                if learning_result and learning_result.get("status") != "completed":
                    issue = {
                        "stage": "ollama_knowledge_extraction",
                        "error": "; ".join(learning_result.get("errors") or [
                            f"unexpected status: {learning_result.get('status')}"
                        ]),
                    }
                    report["stages"]["ollama_knowledge_extraction"]["ok"] = False
                    report["issues"].append(issue)
        approved_ids = []
        if learning_result and learning_result.get("status") == "completed":
            reviewer = KnowledgeReviewService(manager)
            for unit in learning_result.get("knowledge_units", []):
                reviewed = stage(
                    f"knowledge_approve:{unit['knowledge_unit_id']}",
                    lambda unit=unit: reviewer.review(
                        project_id, unit["knowledge_unit_id"], new_status="approved",
                        reviewer="real-audit-reviewer", comments=["真实流程人工审批"],
                    ),
                )
                if reviewed:
                    approved_ids.append(unit["knowledge_unit_id"])

        scenario_result = stage(
            "scenario_compile",
            lambda: ScenarioWorkflow(manager).run(project_id, {
                "scenario_goal": (
                    "验证潜艇受到主动声自导鱼雷攻击时，按资料执行声诱饵投放、"
                    "高频噪声干扰和规避机动流程，并记录防御过程状态"
                ),
                "simulation_object": "潜艇水下航行仿真系统",
                "target_subsystem": "潜艇防御鱼雷模型",
                "mission_phase": "遭受主动声自导鱼雷攻击阶段", "scale": 1,
                "focus_risks": ["声诱饵航向错误", "干扰频段错误", "规避机动错误", "防御失败"],
                "use_project_defaults": False,
            }).model_dump(mode="json"),
        )
        approved_scenario_id = ""
        if scenario_result:
            first = scenario_result["scenarios"][0]
            validation = scenario_result["validations"][0]
            resolutions = {issue: "真实审查已确认并记录" for issue in validation["blocking_issues"]}
            approved = stage(
                "scenario_approve",
                lambda: manager.scenarios.review_compiled(
                    project_id, first["scenario_id"], accept_non_blocking=True,
                    blocking_resolutions=resolutions, approve=True,
                    reviewer="real-audit-reviewer",
                ),
            )
            if approved:
                approved_scenario_id = first["scenario_id"]

        generation = None
        if approved_scenario_id:
            generation = stage(
                "ollama_test_case_agent",
                lambda: GenerationService(manager, settings=settings).generate_test_cases(
                    GenerationRequest(
                        project_id=project_id, scenario_ids=[approved_scenario_id],
                        case_count=1, case_type="主动声自导鱼雷防御流程测试", requested_mode="agent",
                        use_project_kb=True, use_history=False,
                    )
                ).model_dump(mode="json"),
            )
        if generation:
            stage("excel_export", lambda: str(export_project_excel(manager, project_id)))
            stage("word_export", lambda: str(export_project_word(manager, project_id)))

        def require(condition: bool, stage_name: str, message: str) -> None:
            if condition:
                return
            issue = {"stage": stage_name, "error": message}
            report["issues"].append(issue)
            print(f"[FAIL] {stage_name}: {message}")

        pdf_details = report["stages"].get("pdf_worker", {}).get("document", {})
        parse_report = pdf_details.get("parse_report") or {}
        require(
            pdf_details.get("processing_status") == "completed"
            and parse_report.get("total_pages") == 226
            and parse_report.get("ocr_processed_pages") == 225,
            "pdf_worker_invariants",
            "真实226页PDF未完成225页RapidOCR处理并到达completed",
        )
        require(
            (equipment_report or {}).get("success_count") == 5800,
            "equipment_import_invariants",
            "真实military.jsonl未成功导入全部5800条记录",
        )
        require(
            bool(approved_ids),
            "learning_invariants",
            "Ollama未生成并审批任何结构化知识",
        )
        compiled = (scenario_result or {}).get("scenarios") or []
        require(
            bool(compiled and compiled[0].get("knowledge_unit_ids")),
            "scenario_grounding_invariants",
            "编译场景未引用approved知识",
        )
        generated_cases = (generation or {}).get("cases") or []
        generated_case = generated_cases[0].get("case", {}) if generated_cases else {}
        used_tools = set((generation or {}).get("used_tool_names") or [])
        require(
            (generation or {}).get("generation_mode") == "agent"
            and not (generation or {}).get("fallback_reason"),
            "agent_mode_invariants",
            "真实生成未使用Agent模式或发生rule_fallback",
        )
        require(
            {
                "get_compiled_scenario",
                "get_scenario_equipment_allocation",
                "get_scenario_validation_result",
            }.issubset(used_tools),
            "agent_tool_invariants",
            "Agent未实际读取场景、装备分配和校验结果",
        )
        require(
            approved_scenario_id in (generated_case.get("scenario_ids") or [])
            and bool(generated_case.get("source_chunk_ids")),
            "agent_provenance_invariants",
            "生成用例未保留实际场景或来源片段",
        )
        require(
            len(generated_case.get("test_steps") or [])
            == len(generated_case.get("expected_results") or []),
            "step_result_invariants",
            "测试步骤与预期结果未一一对应",
        )
        generated_text = json.dumps(generated_case, ensure_ascii=False)
        require(
            all(term in generated_text for term in ("主动声自导鱼雷", "声诱饵", "高频噪声干扰")),
            "case_domain_invariants",
            "生成用例未覆盖资料支持的主动声自导鱼雷、声诱饵和高频噪声干扰流程",
        )
        require(
            not any(term in generated_text for term in (
                "备用推进", "限制航速", "数据丢失", "系统故障", "干扰器失效"
            )),
            "case_unsupported_fact_invariants",
            "生成用例包含当前场景和来源未支持的系统行为",
        )

        report["summary"] = {
            "project_id": project_id,
            "equipment_success": (equipment_report or {}).get("success_count", 0),
            "approved_knowledge_count": len(approved_ids),
            "scenario_id": approved_scenario_id,
            "agent_generation_mode": (generation or {}).get("generation_mode"),
            "agent_fallback_reason": (generation or {}).get("fallback_reason", ""),
        }

    report["finished_at"] = datetime.now().isoformat()
    report["passed"] = not report["issues"]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"report: {args.report}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
