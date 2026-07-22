"""Application service consumed by Streamlit pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from config.settings import Settings, settings as default_settings
from chains.test_case_review import TestCaseReviewChain
from core.context_builder import ContextBuilder
from core.project_document_exporter import (
    build_project_export_rows,
    export_project_excel,
    export_project_markdown,
    export_project_word,
)
from core.project_kb import search_project_chunks
from core.project_profile_extractor import extract_project_profile
from core.requirement_extractor import extract_requirements_from_chunks
from core.scenario_card_extractor import extract_and_save_scenario_cards
from core.visual_client import OllamaVisualClient
from prompts.visual_evidence_prompt import build_visual_evidence_prompt
from services.generation_service import (
    GenerationRequest,
    GenerationResult,
    GenerationService,
)
from services.review_service import ReviewService
from services.upload_service import UploadService
from learning.job_service import DocumentJobService
from learning.learning_task_service import LearningTaskService
from learning.knowledge_conflict_detector import KnowledgeConflictDetector
from learning.knowledge_review_service import KnowledgeReviewService
from learning.feedback_learning_service import FeedbackLearningService
from equipment.equipment_service import EquipmentService
from equipment.jsonl_importer import MilitaryJsonlImporter
from infrastructure.db.json_codec import loads_json
from scenario_engine.scenario_workflow import ScenarioWorkflow


class UIApplicationService:
    """Keep Streamlit free of persistence, model, prompt, and filesystem orchestration."""

    def __init__(
        self,
        manager: Any,
        case_library: Any = None,
        settings: Settings = default_settings,
    ) -> None:
        self.manager = manager
        self.case_library = case_library
        self.settings = settings
        self.uploads = UploadService(manager, settings)
        self.generation = GenerationService(manager, case_library, settings=settings)
        self.reviews = ReviewService(manager, TestCaseReviewChain(settings))
        self.document_jobs = DocumentJobService(manager)
        self.learning_tasks = LearningTaskService(manager)
        self.knowledge_conflicts = KnowledgeConflictDetector(manager)
        self.knowledge_reviews = KnowledgeReviewService(manager)
        self.feedback_learning = FeedbackLearningService(manager)
        self.equipment_service = EquipmentService(manager.equipment)

    def __getattr__(self, name: str) -> Any:
        """Temporary façade forwarding while page-specific query services are extracted."""
        return getattr(self.manager, name)

    def ingest_document(
        self, project_id: str, filename: str, content: bytes
    ) -> Dict[str, object]:
        return self.uploads.ingest_document(project_id, filename, content)

    def list_document_jobs(self, project_id: str) -> List[Dict[str, Any]]:
        return self.document_jobs.list_jobs(project_id)

    def pause_document_job(self, project_id: str, job_id: str) -> bool:
        return self.document_jobs.pause(project_id, job_id)

    def resume_document_job(self, project_id: str, job_id: str) -> bool:
        return self.document_jobs.resume(project_id, job_id)

    def cancel_document_job(self, project_id: str, job_id: str) -> bool:
        return self.document_jobs.cancel(project_id, job_id)

    def create_learning_task(self, project_id: str, **data: Any) -> Any:
        return self.learning_tasks.create_task(project_id, **data)

    def run_learning_task(self, project_id: str, task_id: str) -> Dict[str, Any]:
        return self.learning_tasks.run_task(project_id, task_id)

    def detect_knowledge_conflicts(self, project_id: str) -> List[Dict[str, Any]]:
        return self.knowledge_conflicts.detect(project_id)

    def review_knowledge(self, project_id: str, knowledge_unit_id: str, **data: Any) -> Dict[str, Any]:
        return self.knowledge_reviews.review(project_id, knowledge_unit_id, **data)

    def scenario_knowledge(self, project_id: str, **scope: Any) -> List[Dict[str, Any]]:
        return self.knowledge_reviews.query_for_scenario(project_id, **scope)

    def document_summaries(self, project_id: str) -> List[Dict[str, Any]]:
        counts: Dict[str, int] = {}
        for row in self.manager.list_chunks(project_id, limit=100000):
            document_id = str(row.get("document_id") or "")
            counts[document_id] = counts.get(document_id, 0) + 1
        result = []
        for document in self.manager.list_documents(project_id):
            report = loads_json(document.get("parse_report_json"), {})
            result.append({
                **document,
                "parser": document.get("parser_type") or report.get("parser_type") or "",
                "total_pages": report.get("total_pages") or 0,
                "ocr_processed_pages": report.get("ocr_processed_pages") or 0,
                "ocr_low_confidence_pages": (
                    report.get("ocr_low_confidence_pages") or 0
                ),
                "possible_scanned_pages": report.get("possible_scanned_pages") or 0,
                "chunk_count": counts.get(str(document["document_id"]), 0),
                "warnings": report.get("warnings") or [],
            })
        return result

    def import_equipment_jsonl(
        self, project_id: str, filename: str, content: bytes, scope: str
    ) -> Dict[str, Any]:
        target_scope = "GLOBAL" if scope == "GLOBAL" else project_id
        if target_scope == "GLOBAL":
            self.manager.equipment.ensure_global_project()
        target = self.uploads.save(target_scope, filename, content, {".jsonl"})
        return MilitaryJsonlImporter(self.manager.equipment).import_file(
            target, target_scope
        ).model_dump(mode="json")

    def search_equipment_ui(
        self, project_id: str, *, name: str = "", category: str = "",
        role: str = "", capability: str = "", allow_global: bool = False,
    ) -> Dict[str, Any]:
        return self.equipment_service.search(
            project_id, name=name, category=category,
            roles=[role] if role else [], capabilities=[capability] if capability else [],
            allow_global=allow_global,
        ).model_dump(mode="json")

    def list_learning_tasks(self, project_id: str) -> List[Dict[str, Any]]:
        return self.learning_tasks.list_tasks(project_id)

    def list_knowledge_for_review(self, project_id: str, **filters: Any) -> List[Dict[str, Any]]:
        return self.knowledge_reviews.list_knowledge(project_id, **filters)

    def list_knowledge_conflicts(self, project_id: str, status: str = "") -> List[Dict[str, Any]]:
        return self.knowledge_reviews.list_conflicts(project_id, status)

    def debug_knowledge_search(self, project_id: str, query: str) -> List[Dict[str, Any]]:
        needle = query.casefold().strip()
        rows = self.knowledge_reviews.query_for_scenario(project_id)
        result = []
        for row in rows:
            text = f"{row.get('title', '')} {row.get('content', '')}".casefold()
            if needle and needle not in text:
                continue
            result.append({**row, "score": 100.0 if needle in str(row.get("title", "")).casefold() else 70.0,
                           "match_reason": "approved知识名称/内容匹配",
                           "source": {"document_id": row.get("document_id"),
                                      "chunk_id": row.get("chunk_id"), "page_no": row.get("page_no")}})
        return result

    def debug_template_search(self, project_id: str, query: str) -> List[Dict[str, Any]]:
        needle = query.casefold().strip()
        with self.manager.connections.connection() as conn:
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM scenario_templates WHERE project_id=? AND status='approved' ORDER BY name",
                (project_id,),
            )]
        result = []
        for row in rows:
            payload = loads_json(row.get("template_json"), {})
            text = f"{row.get('name', '')} {row.get('scenario_category', '')} {payload}".casefold()
            if needle and needle not in text:
                continue
            result.append({
                "template_id": row["template_id"], "name": row["name"],
                "score": 100.0 if needle in row["name"].casefold() else 60.0,
                "match_reason": "approved模板名称/内容匹配", "template": payload,
                "source": {"document_id": row.get("document_id"),
                           "chunk_id": row.get("chunk_id"), "page_no": row.get("page_no")},
            })
        return result

    def debug_retrieval(self, project_id: str, query: str, top_k: int) -> Dict[str, Any]:
        equipment = self.search_equipment_ui(project_id, name=query)
        return {
            "documents": self.search_documents(project_id, query, top_k),
            "approved_knowledge": self.debug_knowledge_search(project_id, query),
            "scenario_templates": self.debug_template_search(project_id, query),
            "equipment_candidates": [*equipment["matches"], *equipment["rejected_matches"]],
            "equipment_missing_information": equipment["missing_information"],
        }

    def save_history_upload(
        self, project_id: str, filename: str, content: bytes
    ) -> Path:
        return self.uploads.save_history(project_id, filename, content)

    def process_visual_upload(
        self, project_id: str, filename: str, content: bytes
    ) -> Dict[str, Any]:
        target = self.uploads.save_image(project_id, filename, content)
        asset_id = self.manager.save_project_asset(
            project_id,
            {
                "asset_type": "image",
                "file_path": str(target),
                "source_document": target.name,
            },
        )
        result = OllamaVisualClient().generate_json_with_images(
            build_visual_evidence_prompt(
                {
                    "asset_id": asset_id,
                    "project_id": project_id,
                    "asset_type": "image",
                    "file_path": str(target),
                    "source_document": target.name,
                }
            ),
            image_paths=[target],
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "asset_id": asset_id,
                "error": str(result.get("error") or "visual extraction failed"),
            }
        data = result.get("data") or {}
        if not isinstance(data, dict):
            return {
                "ok": False,
                "asset_id": asset_id,
                "error": "visual structured output is not an object",
            }
        data = dict(data)
        data["raw_response"] = {"raw": result.get("raw", ""), "parsed": dict(data)}
        evidence_id = self.manager.save_visual_evidence(project_id, asset_id, data)
        return {
            "ok": True,
            "asset_id": asset_id,
            "evidence_id": evidence_id,
            "data": data,
        }

    def search_documents(
        self, project_id: str, query: str, top_k: int
    ) -> List[Dict[str, Any]]:
        return search_project_chunks(self.manager, project_id, query, top_k)

    def extract_profile(self, project_id: str, use_ollama: bool) -> Dict[str, Any]:
        text = self.manager.combined_project_text(project_id)
        project = self.manager.get_project(project_id) or {}
        profile = extract_project_profile(
            text, project.get("project_name", ""), use_ollama=use_ollama
        )
        self.manager.save_profile(project_id, profile)
        return profile

    def extract_requirements(self, project_id: str) -> List[Dict[str, Any]]:
        rows = extract_requirements_from_chunks(
            self.manager.list_chunks(project_id, 2000)
        )
        self.manager.replace_requirements(project_id, rows)
        return rows

    def extract_scenarios(
        self, project_id: str, use_ollama: bool
    ) -> List[Dict[str, Any]]:
        return extract_and_save_scenario_cards(
            self.manager, project_id, use_ollama=use_ollama
        )

    def preview_generation(
        self,
        project_id: str,
        requirement_id: str,
        case_type: str,
        top_k: int,
        use_kb: bool,
        use_history: bool,
    ) -> Dict[str, Any]:
        return ContextBuilder(self.manager, self.case_library).build(
            project_id,
            requirement_id,
            case_type,
            top_k,
            top_k,
            use_kb,
            use_history,
            persist=False,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        return self.generation.generate_test_cases(request)

    def compile_scenario(
        self, project_id: str, intent: Dict[str, Any], progress_callback: Any = None
    ) -> Dict[str, Any]:
        return ScenarioWorkflow(self.manager).run(
            project_id, intent, progress_callback=progress_callback
        ).model_dump(mode="json")

    def record_generation_feedback(self, value: Dict[str, Any]) -> Dict[str, Any]:
        return self.feedback_learning.record_correction(value)

    def list_feedback_candidates(self, project_id: str, status: str = "") -> List[Dict[str, Any]]:
        return self.feedback_learning.list_candidates(project_id, status)

    def approve_feedback_candidate(
        self, project_id: str, candidate_id: str, **review: Any
    ) -> Dict[str, Any]:
        return self.feedback_learning.approve_candidate(project_id, candidate_id, **review)

    def revoke_feedback_rule(
        self, project_id: str, rule_id: str, **review: Any
    ) -> Dict[str, Any]:
        return self.feedback_learning.revoke_rule(project_id, rule_id, **review)

    def explain_feedback_rules(
        self, project_id: str, rule_ids: List[str], *, allow_global: bool = False
    ) -> List[Dict[str, Any]]:
        return self.feedback_learning.explain_generation(
            project_id, rule_ids, allow_global=allow_global
        )

    def list_compiled_scenarios(
        self, project_id: str, status: str = ""
    ) -> List[Dict[str, Any]]:
        return self.manager.scenarios.list_compiled(project_id, status)

    def review_compiled_scenario(
        self, project_id: str, scenario_id: str, **review: Any
    ) -> Dict[str, Any]:
        return self.manager.scenarios.review_compiled(project_id, scenario_id, **review)

    def generate_from_approved_scenario(
        self,
        project_id: str,
        scenario_id: str,
        *,
        case_count: int,
        case_type: str,
        requested_mode: str,
        use_history: bool,
    ) -> GenerationResult:
        scenario = next(
            (row for row in self.manager.scenarios.list_compiled(project_id, "approved")
             if row.get("scenario_id") == scenario_id),
            None,
        )
        if not scenario:
            raise ValueError("场景尚未批准或不属于当前项目")
        requirement_ids = list(scenario.get("requirement_ids") or [])
        return self.generate(GenerationRequest(
            project_id=project_id, requirement_ids=requirement_ids,
            scenario_ids=[scenario_id], case_type=case_type, case_count=case_count,
            requested_mode=requested_mode, use_project_kb=True,
            use_history=use_history,
            additional_instructions="优先使用已批准编译场景，不得重新计算装备数量。",
        ))

    def review_project(self, project_id: str, include_llm: bool = False) -> List[Any]:
        return self.reviews.review_project(project_id, include_llm)

    def confirm_case_update(
        self, project_id: str, case_id: str, case: Dict[str, Any], run_id: str,
        created_by: str = "streamlit-user",
    ) -> None:
        existing = next(
            (row.get("case_json") or {} for row in self.manager.list_generated_cases(project_id)
             if row.get("case_id") == case_id),
            {},
        )
        self.reviews.confirm_update(project_id, case_id, case, run_id)
        ignored = {"case_id", "updated_at", "created_at", "provenance"}
        for field_name in sorted((set(existing) | set(case)) - ignored):
            if existing.get(field_name) == case.get(field_name):
                continue
            self.feedback_learning.record_correction({
                "original_value": existing.get(field_name),
                "corrected_value": case.get(field_name),
                "field_name": field_name,
                "entity_type": "test_case",
                "correction_reason": "人工审核修改测试用例",
                "project_id": project_id,
                "scenario_id": str((case.get("scenario_ids") or [""])[0]),
                "requirement_ids": list(case.get("requirement_ids") or []),
                "source_context": {
                    "generation_run_id": run_id,
                    "source_chunk_ids": list(case.get("source_chunk_ids") or []),
                },
                "created_by": created_by,
            })

    def export_rows(self, project_id: str) -> Dict[str, List[Dict[str, Any]]]:
        return build_project_export_rows(self.manager, project_id)

    def export_excel(self, project_id: str) -> Path:
        return export_project_excel(self.manager, project_id)

    def export_word(self, project_id: str) -> Path:
        return export_project_word(self.manager, project_id)

    def export_markdown(self, project_id: str) -> Path:
        return export_project_markdown(self.manager, project_id)
