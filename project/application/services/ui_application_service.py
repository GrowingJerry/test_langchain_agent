"""Application service consumed by Streamlit pages."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import inspect
import logging
import time
from infrastructure.runtime_diagnostics import configure_runtime_logging
from infrastructure.repositories.base import now_iso
from infrastructure.database.json_codec import dumps_json

from config.settings import Settings, settings as default_settings
from chains.test_case_review import TestCaseReviewChain
from application.services.generation_context import ContextBuilder
from infrastructure.exporters.project_documents import (
    build_project_export_rows,
    export_project_excel,
    export_project_markdown,
    export_project_word,
)
from infrastructure.retrieval.project_knowledge import search_project_chunks
from workflows.learning.profile_extractor import extract_project_profile
from workflows.learning.structured_requirement_extractor import (
    extract_structured_requirements,
    extract_structured_requirements_with_report,
)
from workflows.scenario.card_extractor import extract_and_save_scenario_cards
from infrastructure.llm.visual_client import OllamaVisualClient
from infrastructure.llm.visual_prompt import build_visual_evidence_prompt
from application.services.generation_service import (
    GenerationRequest,
    GenerationResult,
    GenerationService,
)
from application.services.review_service import ReviewService
from application.services.upload_service import UploadService
from workflows.learning.job_service import DocumentJobService
from workflows.learning.learning_task_service import LearningTaskService
from workflows.learning.knowledge_conflict_detector import KnowledgeConflictDetector
from workflows.learning.knowledge_review_service import KnowledgeReviewService
from workflows.learning.feedback_learning_service import FeedbackLearningService
from infrastructure.equipment.equipment_service import EquipmentService
from infrastructure.equipment.jsonl_importer import MilitaryJsonlImporter
from infrastructure.database.json_codec import loads_json
from domain.schemas.project import VisualEvidence
from workflows.scenario.scenario_workflow import ScenarioWorkflow

configure_runtime_logging()
logger = logging.getLogger("test_agent.ui_service")


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
        self,
        project_id: str,
        filename: str,
        content: bytes,
        requirement_id: str = "",
    ) -> Dict[str, Any]:
        requirement_id = str(requirement_id or "").strip()
        requirement = None
        if requirement_id:
            requirement = self.manager.get_requirement(project_id, requirement_id)
            if not requirement:
                return {
                    "ok": False,
                    "asset_id": "",
                    "error": f"关联需求不存在或不属于当前项目：{requirement_id}",
                }
        target = self.uploads.save_image(project_id, filename, content)
        asset_id = self.manager.save_project_asset(
            project_id,
            {
                "asset_type": "image",
                "file_path": str(target),
                "source_document": target.name,
            },
        )
        result = OllamaVisualClient(
            base_url=self.settings.ollama_base_url,
            model=self.settings.ollama_vision_model,
            timeout=self.settings.ollama_vision_timeout,
        ).generate_json_with_images(
            build_visual_evidence_prompt(
                {
                    "asset_id": asset_id,
                    "project_id": project_id,
                    "asset_type": "image",
                    "file_path": str(target),
                    "source_document": target.name,
                    "requirement_id": requirement_id,
                    "requirement_title": (requirement or {}).get("title", ""),
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
        raw_data = dict(data)
        data["main_objects"] = [
            str(item.get("name") or item.get("description") or "").strip()
            if isinstance(item, dict)
            else str(item).strip()
            for item in data.get("main_objects", [])
        ]
        data["main_objects"] = [item for item in data["main_objects"] if item]
        data.update({
            "project_id": project_id,
            "asset_id": asset_id,
            "requirement_id": requirement_id,
            "related_requirement_ids": [requirement_id] if requirement_id else [],
            # Machine-extracted visual evidence must be reviewed before it can
            # be treated as confirmed project evidence, regardless of the
            # model's self-reported confidence.
            "need_human_confirm": True,
            "raw_response": {"raw": result.get("raw", ""), "parsed": raw_data},
        })
        try:
            data = VisualEvidence.model_validate(data).model_dump(mode="json")
        except (TypeError, ValueError) as exc:
            return {
                "ok": False,
                "asset_id": asset_id,
                "error": f"visual structured output validation failed: {exc}",
            }
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
        rows = extract_structured_requirements(self.manager, project_id)
        self.manager.replace_requirements(project_id, rows)
        return rows

    def preview_requirement_extraction(self, project_id: str) -> Dict[str, Any]:
        return extract_structured_requirements_with_report(self.manager, project_id)

    def save_reviewed_requirements(
        self,
        project_id: str,
        rows: List[Dict[str, Any]],
        machine_rows: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        machine_by_id = {
            str(row.get("requirement_id") or ""): dict(row) for row in machine_rows
        }
        reviewed = []
        for row in rows:
            if not row.get("retained", True):
                continue
            original = machine_by_id.get(str(row.get("requirement_id") or ""), {})
            changes = {
                key: row.get(key)
                for key in (
                    "requirement_id",
                    "title",
                    "description",
                    "requirement_type",
                    "recommended_test_type",
                    "alternative_test_types",
                    "inputs",
                    "outputs",
                    "exception_rules",
                    "need_human_confirm",
                )
                if original.get(key) != row.get(key)
            }
            reviewed.append({
                **row,
                "machine_extraction": original or row,
                "review_changes": changes,
                "retained": True,
            })
        self.manager.replace_requirements(project_id, reviewed)
        return reviewed

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
        context = ContextBuilder(self.manager, self.case_library).build(
            project_id,
            requirement_id,
            case_type,
            top_k,
            top_k,
            use_kb,
            use_history,
            persist=False,
        )
        from application.services.generation_package import build_generation_package
        return build_generation_package(
            self.manager,
            context,
            num_ctx=self.settings.ollama_num_ctx,
            num_predict=self.settings.ollama_structured_num_predict,
            case_type=case_type,
            settings=self.settings,
        )

    def generate(
        self, request: GenerationRequest, progress_callback: Any = None,
        cancellation_callback: Any = None,
    ) -> GenerationResult:
        return self.generation.generate_test_cases(
            request, progress_callback=progress_callback, cancellation_callback=cancellation_callback
        )

    def generate_requirement_batch(
        self,
        base_request: GenerationRequest,
        requirement_ids: List[str],
        batch_id: str,
        progress_callback: Any = None,
        force: bool = False,
        cancellation_callback: Any = None,
    ) -> Dict[str, Any]:
        """Generate requirements sequentially and checkpoint every completed item."""
        ordered_ids = list(dict.fromkeys(str(item) for item in requirement_ids if item))
        completed = set(
            self.manager.completed_batch_requirements(base_request.project_id, batch_id)
        )
        summary: Dict[str, Any] = {
            "batch_id": batch_id,
            "total": len(ordered_ids),
            "completed": [],
            "skipped": [],
            "failed": [],
            "cases": [],
            "diagnostic_runs": [],
        }
        for index, requirement_id in enumerate(ordered_ids, 1):
            if cancellation_callback and cancellation_callback():
                summary["cancelled"] = True
                break
            if requirement_id in completed and not force:
                summary["skipped"].append(requirement_id)
                if progress_callback:
                    progress_callback({
                        "kind": "batch",
                        "content": f"[{index}/{len(ordered_ids)}] {requirement_id} 已完成，断点续跑跳过。",
                        "requirement_id": requirement_id,
                        "index": index,
                        "total": len(ordered_ids),
                    })
                continue
            request = base_request.model_copy(update={
                "requirement_ids": [requirement_id],
                "scenario_ids": [],
                "batch_id": batch_id,
            })

            def relay(event: Dict[str, Any]) -> None:
                if progress_callback:
                    progress_callback({
                        **event,
                        "requirement_id": requirement_id,
                        "index": index,
                        "total": len(ordered_ids),
                    })

            try:
                relay({"kind": "batch", "content": f"开始生成 {requirement_id}"})
                generate_params = inspect.signature(self.generate).parameters
                kwargs = {"progress_callback": relay}
                if "cancellation_callback" in generate_params:
                    kwargs["cancellation_callback"] = cancellation_callback
                result = self.generate(request, **kwargs)
                dumped = result.model_dump(mode="json")
                summary["cases"].extend(dumped.get("cases") or [])
                summary["diagnostic_runs"].append({
                    "requirement_id": requirement_id,
                    "generation_mode": dumped.get("generation_mode"),
                    "agent_failure": dumped.get("agent_failure"),
                    "context_fingerprints": dumped.get("context_fingerprints") or [],
                    "agent_direct_context_equal": dumped.get("agent_direct_context_equal"),
                    "diagnostic_run_id": dumped.get("diagnostic_run_id"),
                    "diagnostic_log_path": dumped.get("diagnostic_log_path"),
                    "diagnostic_bundle_path": dumped.get("diagnostic_bundle_path"),
                    "warnings": dumped.get("warnings") or [],
                })
                self.manager.create_generation_run(
                    base_request.project_id,
                    "requirement_batch_checkpoint",
                    status="completed",
                    metadata={
                        "batch_id": batch_id,
                        "requirement_id": requirement_id,
                        "generation_run_id": result.generation_run_id,
                        "case_count": len(result.cases),
                    },
                )
                summary["completed"].append(requirement_id)
                relay({"kind": "batch", "content": f"{requirement_id} 已生成并保存。"})
            except Exception as exc:
                summary["failed"].append({
                    "requirement_id": requirement_id,
                    "error": f"{type(exc).__name__}: {exc}",
                })
                relay({"kind": "error", "content": summary["failed"][-1]["error"]})
        return summary

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

    def analyze_csci_docx(self, project_id: str, path: Path) -> Dict[str, Any]:
        from application.services.csci_document_service import parse_formal_csci_docx
        from application.services.traceability_service import atomic_indicators
        from infrastructure.database.json_codec import dumps_json
        started=time.monotonic()
        logger.info("CSCI parse started project=%s file=%s size=%s",project_id,path.name,path.stat().st_size)
        try:
            parsed=parse_formal_csci_docx(path); nodes=parsed["nodes"]
        except Exception:
            logger.exception("CSCI parse failed project=%s file=%s",project_id,path)
            raise
        indicators = [item for node in parsed["testable_nodes"] for item in atomic_indicators(node)]
        with self.manager.connections.transaction() as conn:
            for node in nodes:
                conn.execute("INSERT OR REPLACE INTO requirement_nodes(project_id,node_id,parent_id,identifier,name,level,hierarchy_path_json,sections_json,source_document,source_block_id,identifier_generated,need_human_confirm,ancestor_node_ids_json,ancestor_identifiers_json,node_type,source_position_json,section_evidence_json,overview_node_id,testable,review_status,enabled,deleted_at,generation_approved,extraction_method,confidence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (project_id,node.node_id,node.parent_id,node.identifier,node.name,node.level,dumps_json(node.hierarchy_path),dumps_json(node.sections),node.source_document,node.source_block_id,int(node.identifier_generated),int(node.need_human_confirm),dumps_json(node.ancestor_node_ids),dumps_json(node.ancestor_identifiers),node.node_type,dumps_json(node.source_position),dumps_json(node.section_evidence),node.overview_node_id,int(node.testable),'pending',1,None,0,'csci_structured',float(parsed.get('structure_confidence',0))))
            for item in indicators:
                conn.execute("INSERT OR REPLACE INTO requirement_indicators(project_id,indicator_id,capability_id,function_id,parent_indicator_id,indicator_text,indicator_type,source_json,rules_json,verification_scope,need_human_confirm) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (project_id,item.indicator_id,item.capability_id,item.function_id,item.parent_indicator_id,item.indicator_text,item.indicator_type,
                     dumps_json({"section":item.source_section,"block_id":item.source_block_id,"source_text":item.source_text}),
                     dumps_json({"inputs":item.input_constraints,"processing":item.processing_rules,"expected":item.expected_behavior,"exceptions":item.exception_rules}),item.verification_scope,int(item.need_human_confirm)))
        # The structured parser is an enhanced requirement extraction path, not a
        # separate feature island.  Publish every leaf into the canonical project
        # requirement store so the normal generation flow can use it immediately.
        for node in nodes:
            node_indicators = [item for item in indicators if item.function_id == node.identifier]
            self.manager.upsert_requirement(project_id, {
                "requirement_id": node.identifier,
                "title": node.name,
                "description": node.sections.get("功能描述", ""),
                "category": "功能需求",
                "requirement_type": "functional",
                "section_number": node.section_number,
                "section_path": list(node.hierarchy_path),
                "inputs": [node.sections.get("输入", "")] if node.sections.get("输入") else [],
                "processing_rules": [node.sections.get("处理", "")] if node.sections.get("处理") else [],
                "outputs": [node.sections.get("输出", "")] if node.sections.get("输出") else [],
                "source_document": node.source_document,
                "source_documents": [node.source_document],
                "source_chunk_id": node.source_block_id,
                "source_chunk_ids": [node.source_block_id],
                "source_evidence": [
                    {
                        "block_id": node.source_block_id,
                        "section_path": list(node.hierarchy_path),
                        "indicator_ids": [item.indicator_id for item in node_indicators],
                        "text": node.sections.get("功能描述", ""),
                    }
                ],
                "parent_requirement_ids": [node.parent_id] if node.parent_id else [],
                "need_human_confirm": bool(node.need_human_confirm),
                "missing_information": [],
                "retained": False,
            })
        section_32=len(parsed.get("overview_nodes") or [])
        section_33=len(parsed.get("detail_nodes") or [])
        warnings=list(parsed.get("warnings") or [])
        if not nodes: warnings.append("未识别到3.2/3.3需求节点；请检查标题编号和DOCX正文结构。")
        if nodes and not parsed["testable_nodes"]: warnings.append("已识别需求树，但没有具备完整功能描述/输入/处理/输出的最低可测功能。")
        elapsed=round(time.monotonic()-started,3)
        logger.info("CSCI parse completed project=%s nodes=%s testable=%s elapsed=%s",project_id,len(nodes),len(parsed["testable_nodes"]),elapsed)
        return {"section_32_count":section_32,"section_33_count":section_33,"node_count":len(nodes),"testable_count":len(parsed["testable_nodes"]),"warnings":warnings,"elapsed_seconds":elapsed,"extraction_method":"csci_structured","fallback_reason":"","structure_confidence":parsed.get("structure_confidence",0),"nodes":[x.model_dump(mode="json") for x in nodes],"overview_nodes":[x.model_dump(mode="json") for x in parsed["overview_nodes"]],"testable_nodes":[x.model_dump(mode="json") for x in parsed["testable_nodes"]],"indicators":[x.model_dump(mode="json") for x in indicators]}

    def extract_requirement_document(self,project_id:str,path:Path,mode:str="auto",progress_callback:Any=None)->Dict[str,Any]:
        """Route both extraction methods into the canonical node/indicator stores."""
        from application.services.csci_document_service import parse_formal_csci_docx
        if progress_callback: progress_callback({"stage":"结构可信度检测","index":0,"total":1,"object":path.name})
        probe=parse_formal_csci_docx(path)
        use_csci=mode=="csci" or (mode=="auto" and probe.get("structure_confidence",0)>=.8)
        if use_csci:
            result=self.analyze_csci_docx(project_id,path)
            if not result["testable_count"]: raise ValueError("规范CSCI抽取未产生最低可测功能")
            return result
        if mode=="csci": raise ValueError("文档不满足规范CSCI语义结构")
        from application.services.general_requirement_service import extract_general_requirements
        from application.services.traceability_service import atomic_indicators
        from infrastructure.database.json_codec import dumps_json
        parsed=extract_general_requirements(path,self.settings,progress_callback); nodes=parsed["nodes"]
        if not nodes: raise ValueError("通用AI抽取未产生任何有效需求")
        indicators=[item for node in nodes for item in atomic_indicators(node)]
        with self.manager.connections.transaction() as conn:
            for node in nodes:
                conn.execute("INSERT OR REPLACE INTO requirement_nodes(project_id,node_id,parent_id,identifier,name,level,hierarchy_path_json,sections_json,source_document,source_block_id,identifier_generated,need_human_confirm,ancestor_node_ids_json,ancestor_identifiers_json,node_type,source_position_json,section_evidence_json,overview_node_id,testable,review_status,enabled,deleted_at,generation_approved,extraction_method,confidence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(project_id,node.node_id,node.parent_id,node.identifier,node.name,node.level,dumps_json(node.hierarchy_path),dumps_json(node.sections),node.source_document,node.source_block_id,int(node.identifier_generated),int(node.need_human_confirm),dumps_json(node.ancestor_node_ids),dumps_json(node.ancestor_identifiers),node.node_type,dumps_json(node.source_position),dumps_json(node.section_evidence),node.overview_node_id,1,'pending',1,None,0,'llm_general',float(parsed.get('structure_confidence',0))))
            for item in indicators:
                conn.execute("INSERT OR REPLACE INTO requirement_indicators(project_id,indicator_id,capability_id,function_id,parent_indicator_id,indicator_text,indicator_type,source_json,rules_json,verification_scope,need_human_confirm) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(project_id,item.indicator_id,item.capability_id,item.function_id,item.parent_indicator_id,item.indicator_text,item.indicator_type,dumps_json({"block_id":item.source_block_id,"source_text":item.source_text}),dumps_json({"inputs":item.input_constraints,"processing":item.processing_rules,"expected":item.expected_behavior}),item.verification_scope,int(item.need_human_confirm)))
        for node in nodes:
            self.manager.upsert_requirement(project_id,{"requirement_id":node.identifier,"title":node.name,"description":node.sections.get("功能描述",""),"requirement_type":"functional","section_path":node.hierarchy_path,"inputs":[node.sections.get("输入","")],"processing_rules":[node.sections.get("处理","")],"outputs":[node.sections.get("输出","")],"source_document":node.source_document,"source_chunk_id":node.source_block_id,"need_human_confirm":node.need_human_confirm,"retained":False})
        return {"section_32_count":0,"section_33_count":0,"node_count":len(nodes),"testable_count":len(nodes),"warnings":parsed["warnings"],"elapsed_seconds":0,"extraction_method":"llm_general","fallback_reason":"CSCI结构可信度不足，自动回退" if mode=="auto" else "用户选择通用AI抽取","structure_confidence":parsed.get("structure_confidence",0),"nodes":[x.model_dump(mode='json') for x in nodes],"testable_nodes":[x.model_dump(mode='json') for x in nodes],"indicators":[x.model_dump(mode='json') for x in indicators]}

    def atomize_and_audit_requirements(self, project_id: str, progress_callback: Any = None, resume: bool = True) -> Dict[str, Any]:
        from application.services.requirement_atomization_service import atomize_and_audit
        from domain.schemas.traceability import RequirementNode
        from infrastructure.database.json_codec import dumps_json, loads_json
        with self.manager.connections.connection() as conn:
            rows=[dict(x) for x in conn.execute("SELECT * FROM requirement_nodes WHERE project_id=? AND enabled=1 AND deleted_at IS NULL ORDER BY level,source_block_id",(project_id,))]
        nodes=[]
        for row in rows:
            if not row.get("testable"): continue
            nodes.append(RequirementNode(node_id=row["node_id"],name=row["name"],identifier=row["identifier"],identifier_generated=bool(row["identifier_generated"]),level=row["level"],parent_id=row.get("parent_id") or "",section_number=row.get("section_number") or "",hierarchy_path=loads_json(row.get("hierarchy_path_json"),[]),ancestor_node_ids=loads_json(row.get("ancestor_node_ids_json"),[]),ancestor_identifiers=loads_json(row.get("ancestor_identifiers_json"),[]),node_type=row.get("node_type") or "function",source_document=row.get("source_document") or "",source_block_id=row.get("source_block_id") or "",source_position=loads_json(row.get("source_position_json"),{}),sections=loads_json(row.get("sections_json"),{}),section_evidence=loads_json(row.get("section_evidence_json"),{}),overview_node_id=row.get("overview_node_id") or "",testable=True))
        results=[]; failures=[]; total=len(nodes)
        for index,node in enumerate(nodes,1):
            if progress_callback: progress_callback({"index":index,"total":total,"function_id":node.identifier,"name":node.name,"status":"running"})
            with self.manager.connections.connection() as conn:
                previous=conn.execute("SELECT review_status FROM requirement_nodes WHERE project_id=? AND node_id=?",(project_id,node.node_id)).fetchone()
                existing_atom_count=conn.execute("SELECT count(*) FROM requirement_indicators WHERE project_id=? AND function_id=?",(project_id,node.identifier)).fetchone()[0]
            if resume and previous and previous[0] in ("passed","human_confirmed") and existing_atom_count:
                results.append({"function_id":node.identifier,"status":"skipped_completed"})
                if progress_callback: progress_callback({"index":index,"total":total,"function_id":node.identifier,"name":node.name,"status":"skipped_completed"})
                continue
            try:
                overview=""
                if node.overview_node_id:
                    with self.manager.connections.connection() as conn:
                        overview_row=conn.execute("SELECT sections_json FROM requirement_nodes WHERE project_id=? AND node_id=?",(project_id,node.overview_node_id)).fetchone()
                    if overview_row: overview=str(loads_json(overview_row[0],{}).get("总体需求概述", ""))
                result=atomize_and_audit(node,overview,self.settings)
                if not result.get("atoms") or not isinstance(result.get("model_audit"),dict) or not result["model_audit"]:
                    raise ValueError("模型返回空原子需求或空审计结果")
                record={"function_id":node.identifier,"status":"completed","atom_count":len(result["atoms"]),"coverage_complete":bool(result["coverage_complete"]),"coverage_score":result["coverage_score"],"audit_summary":{"missing_spans":result["model_audit"].get("missing_spans",[]),"unsupported_atoms":result["model_audit"].get("unsupported_atoms",[]),"review_notes":result["model_audit"].get("review_notes",[])},"deterministic_audit":result["deterministic_audit"]}
                with self.manager.connections.transaction() as conn:
                    conn.execute("DELETE FROM requirement_indicators WHERE project_id=? AND function_id=?",(project_id,node.identifier))
                    for item in result["atoms"]:
                        conn.execute("INSERT INTO requirement_indicators(project_id,indicator_id,capability_id,function_id,parent_indicator_id,indicator_text,indicator_type,source_json,rules_json,verification_scope,need_human_confirm) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(project_id,item.indicator_id,item.capability_id,item.function_id,item.parent_indicator_id,item.indicator_text,item.indicator_type,dumps_json({"section":item.source_section,"block_id":item.source_block_id,"source_text":item.source_text,"evidence_spans":item.evidence_spans,"mandatory_coverage":True}),dumps_json({"inputs":item.input_constraints,"processing":item.processing_rules,"expected":item.expected_behavior}),item.verification_scope,int(item.need_human_confirm)))
                    conn.execute("UPDATE requirement_nodes SET review_status=? WHERE project_id=? AND node_id=?",("passed" if result["coverage_complete"] else "pending",project_id,node.node_id))
                results.append(record)
                if progress_callback: progress_callback({"index":index,"total":total,"function_id":node.identifier,"name":node.name,"status":"completed","atom_count":len(result["atoms"])})
            except Exception as exc:
                logger.exception("Requirement atomization failed project=%s function=%s",project_id,node.identifier)
                failures.append({"function_id":node.identifier,"error":f"{type(exc).__name__}: {exc}"})
                if progress_callback: progress_callback({"index":index,"total":total,"function_id":node.identifier,"name":node.name,"status":"failed","error":str(exc)})
        completed=[x for x in results if x.get("status")=="completed"]
        return {"total":total,"completed":len(completed),"failures":failures,"functions":results,"coverage_complete":bool(total and not failures and all(x.get("coverage_complete",True) for x in results))}

    def save_reviewed_atoms(self, project_id: str, rows: List[Dict[str, Any]]) -> None:
        from hashlib import sha256
        from infrastructure.database.json_codec import dumps_json
        with self.manager.connections.transaction() as conn:
            existing={x["indicator_id"]:dict(x) for x in conn.execute("SELECT * FROM requirement_indicators WHERE project_id=?",(project_id,))}
            retained=set()
            for row in rows:
                indicator_id=str(row.get("indicator_id") or "").strip()
                if not indicator_id:
                    function_id=str(row.get("function_id") or "").strip()
                    text=str(row.get("indicator_text") or "").strip()
                    if not function_id or not text:
                        continue
                    valid=conn.execute("SELECT 1 FROM requirement_nodes WHERE project_id=? AND identifier=? AND testable=1",(project_id,function_id)).fetchone()
                    if not valid:
                        continue
                    indicator_id="HUMAN-"+sha256((project_id+function_id+text).encode("utf-8")).hexdigest()[:16].upper()
                    conn.execute("INSERT INTO requirement_indicators(project_id,indicator_id,capability_id,function_id,parent_indicator_id,indicator_text,indicator_type,source_json,rules_json,verification_scope,need_human_confirm) VALUES(?,?,?,?,?,?,?,?,?,?,0)",(project_id,indicator_id,function_id,function_id,"",text,str(row.get("indicator_type") or "其他"),dumps_json({"review_source":"human_added","mandatory_coverage":True}),dumps_json({}),str(row.get("verification_scope") or "offline_verifiable")))
                    retained.add(indicator_id)
                    continue
                if indicator_id not in existing: continue
                retained.add(indicator_id); conn.execute("UPDATE requirement_indicators SET indicator_text=?,indicator_type=?,need_human_confirm=0 WHERE project_id=? AND indicator_id=?",(str(row.get("indicator_text") or "").strip(),str(row.get("indicator_type") or "其他"),project_id,indicator_id))
            for indicator_id in set(existing)-retained: conn.execute("DELETE FROM requirement_indicators WHERE project_id=? AND indicator_id=?",(project_id,indicator_id))
            conn.execute("UPDATE requirement_nodes SET review_status='human_confirmed' WHERE project_id=? AND testable=1",(project_id,))

    def save_binding_reviews(self, project_id: str, rows: List[Dict[str, Any]]) -> None:
        """Confirm or revise semantic binding rows without exposing locator syntax."""
        with self.manager.connections.transaction() as conn:
            valid_pages={row[0] for row in conn.execute("SELECT page_id FROM html_pages WHERE project_id=?",(project_id,))}
            valid_elements={row[0]:row[1] for row in conn.execute("SELECT element_id,page_id FROM html_elements WHERE project_id=?",(project_id,))}
            for row in rows:
                kind=str(row.get("binding_type") or "page")
                link_id=str(row.get("link_id") or "")
                page_id=str(row.get("page_id") or "")
                element_id=str(row.get("confirmed_element_id") or "")
                confirmed=bool(row.get("confirmed"))
                page_pending=bool(row.get("page_confirmed_element_pending"))
                confidence=float(row.get("confidence") or 0)
                reason=str(row.get("reason") or "")
                if confirmed and (confidence <= 0 or any(word in reason for word in ("无关","不相关","未找到匹配"))):
                    raise ValueError("置信度为0或明确不相关的绑定不能确认；请选择其他页面/元素后重试")
                if not link_id or page_id not in valid_pages:
                    continue
                if kind == "element":
                    if element_id and valid_elements.get(element_id) != page_id:
                        continue
                    conn.execute("UPDATE requirement_element_links SET page_id=?,confirmed_element_id=?,status=?,need_human_confirm=? WHERE project_id=? AND link_id=?",(page_id,element_id,"confirmed" if confirmed and element_id else "proposed",0 if confirmed and element_id else 1,project_id,link_id))
                else:
                    status = "page_confirmed_element_pending" if page_pending else ("confirmed" if confirmed else "proposed")
                    evidence = dumps_json({"human_confirmation":{"confirmed_by":str(row.get("confirmed_by") or "user"),"confirmed_at":now_iso(),"selected_status":status},"original_model":{"confidence":confidence,"reason":reason}})
                    conn.execute("UPDATE requirement_page_links SET page_id=?,status=?,need_human_confirm=?,evidence_json=? WHERE project_id=? AND link_id=?",(page_id,status,0 if status in {"confirmed","page_confirmed_element_pending"} else 1,evidence,project_id,link_id))

    def requirement_review_rows(self,project_id:str)->List[Dict[str,Any]]:
        from infrastructure.database.json_codec import loads_json
        with self.manager.connections.connection() as conn:
            rows=[dict(x) for x in conn.execute("SELECT node_id,parent_id,identifier,name,hierarchy_path_json,sections_json,source_document,source_block_id,testable,review_status,enabled,deleted_at,generation_approved,extraction_method,confidence,need_human_confirm FROM requirement_nodes WHERE project_id=? AND testable=1 ORDER BY source_block_id LIMIT 500",(project_id,))]
        result=[]
        for row in rows:
            sections=loads_json(row.pop('sections_json'),{}); hierarchy=loads_json(row.pop('hierarchy_path_json'),[])
            result.append({**row,"hierarchy_path":" / ".join(hierarchy),"functional_description":sections.get("功能描述",""),"inputs":sections.get("输入",""),"processing":sections.get("处理",""),"outputs":sections.get("输出",""),"deleted":bool(row.get("deleted_at")),"enabled":bool(row.get("enabled")),"generation_approved":bool(row.get("generation_approved")),"need_human_confirm":bool(row.get("need_human_confirm"))})
        return result

    def requirement_review_evidence(self, project_id: str, node_id: str) -> Dict[str, Any]:
        """Return a bounded, human-readable evidence view for one review row."""
        from infrastructure.database.json_codec import loads_json
        with self.manager.connections.connection() as conn:
            node = conn.execute(
                "SELECT identifier,source_document,source_block_id,sections_json,section_evidence_json "
                "FROM requirement_nodes WHERE project_id=? AND node_id=?",
                (project_id, node_id),
            ).fetchone()
            if not node:
                raise KeyError("当前项目中不存在所选需求")
            atoms = [dict(row) for row in conn.execute(
                "SELECT indicator_id,indicator_text,indicator_type,source_json,verification_scope,need_human_confirm "
                "FROM requirement_indicators WHERE project_id=? AND function_id=? ORDER BY indicator_id LIMIT 200",
                (project_id, node["identifier"]),
            )]
            pages = [dict(row) for row in conn.execute(
                "SELECT page_id,status,confidence,reason,need_human_confirm FROM requirement_page_links "
                "WHERE project_id=? AND function_id=? ORDER BY confidence DESC LIMIT 100",
                (project_id, node["identifier"]),
            )]
            elements = [dict(row) for row in conn.execute(
                "SELECT indicator_id,page_id,confirmed_element_id,status,confidence,reason,need_human_confirm "
                "FROM requirement_element_links WHERE project_id=? AND indicator_id IN "
                "(SELECT indicator_id FROM requirement_indicators WHERE project_id=? AND function_id=?) "
                "ORDER BY confidence DESC LIMIT 200",
                (project_id, project_id, node["identifier"]),
            )]
        for atom in atoms:
            source = loads_json(atom.pop("source_json", "{}"), {})
            atom["source_text"] = str(source.get("source_text") or "")[:2000]
            atom["evidence_spans"] = source.get("evidence_spans") or []
        return {
            "source_document": node["source_document"],
            "source_block_id": node["source_block_id"],
            "original_sections": loads_json(node["sections_json"], {}),
            "section_evidence": loads_json(node["section_evidence_json"], {}),
            "atoms": atoms,
            "page_bindings": pages,
            "element_bindings": elements,
        }

    def save_requirement_reviews(self,project_id:str,rows:List[Dict[str,Any]])->Dict[str,int]:
        from infrastructure.database.json_codec import dumps_json,loads_json
        from datetime import datetime,timezone
        updated=deleted=restored=0
        with self.manager.connections.transaction() as conn:
            existing={x['node_id']:dict(x) for x in conn.execute("SELECT * FROM requirement_nodes WHERE project_id=? AND testable=1",(project_id,))}
            for item in rows:
                node_id=str(item.get('node_id') or ''); old=existing.get(node_id)
                if not old:
                    from hashlib import sha256
                    identifier=str(item.get('identifier') or '').strip(); name=str(item.get('name') or '').strip(); description=str(item.get('functional_description') or '').strip()
                    if not identifier or not name or not description: continue
                    node_id='MANUAL-'+sha256((project_id+identifier).encode()).hexdigest()[:16].upper(); hierarchy=[x.strip() for x in str(item.get('hierarchy_path') or name).split('/') if x.strip()]
                    sections={"功能描述":description,"输入":str(item.get('inputs') or ''),"处理":str(item.get('processing') or ''),"输出":str(item.get('outputs') or '')}
                    conn.execute("INSERT INTO requirement_nodes(project_id,node_id,parent_id,identifier,name,level,hierarchy_path_json,sections_json,source_document,source_block_id,identifier_generated,need_human_confirm,node_type,testable,review_status,enabled,generation_approved,extraction_method,confidence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(project_id,node_id,'',identifier,name,len(hierarchy),dumps_json(hierarchy),dumps_json(sections),'manual_input','manual',0,0,'function',1,'human_confirmed',1,0,'manual',1.0))
                    self.manager.requirements._upsert(conn,project_id,{"requirement_id":identifier,"title":name,"description":description,"requirement_type":"functional","section_path":hierarchy,"inputs":[sections['输入']],"processing_rules":[sections['处理']],"outputs":[sections['输出']],"source_document":'manual_input',"source_chunk_id":'manual',"retained":False})
                    updated+=1; continue
                old_identifier=old['identifier']; identifier=str(item.get('identifier') or old_identifier).strip(); name=str(item.get('name') or old['name']).strip()
                if not identifier or not name: raise ValueError('名称和标识符不能为空')
                if identifier!=old_identifier:
                    conflict=conn.execute("SELECT 1 FROM requirement_nodes WHERE project_id=? AND identifier=? AND node_id<>?",(project_id,identifier,node_id)).fetchone()
                    if conflict: raise ValueError(f'标识符已存在: {identifier}')
                    conn.execute("UPDATE requirement_indicators SET function_id=? WHERE project_id=? AND function_id=?",(identifier,project_id,old_identifier)); conn.execute("UPDATE requirement_page_links SET function_id=? WHERE project_id=? AND function_id=?",(identifier,project_id,old_identifier))
                sections=loads_json(old.get('sections_json'),{}); sections.update({"功能描述":str(item.get('functional_description') or ''),"输入":str(item.get('inputs') or ''),"处理":str(item.get('processing') or ''),"输出":str(item.get('outputs') or '')})
                hierarchy=[x.strip() for x in str(item.get('hierarchy_path') or '').split('/') if x.strip()] or [name]; hierarchy[-1]=name
                is_deleted=bool(item.get('deleted')); was_deleted=bool(old.get('deleted_at')); deleted_at=datetime.now(timezone.utc).isoformat() if is_deleted else None; enabled=bool(item.get('enabled')) and not is_deleted
                conn.execute("UPDATE requirement_nodes SET identifier=?,name=?,hierarchy_path_json=?,sections_json=?,enabled=?,deleted_at=?,generation_approved=0,review_status='human_confirmed',need_human_confirm=0 WHERE project_id=? AND node_id=?",(identifier,name,dumps_json(hierarchy),dumps_json(sections),int(enabled),deleted_at,project_id,node_id))
                if is_deleted and not was_deleted: deleted+=1
                if not is_deleted and was_deleted: restored+=1
                updated+=1
                conn.execute("DELETE FROM project_requirements WHERE project_id=? AND requirement_id=?",(project_id,old_identifier))
                self.manager.requirements._upsert(conn,project_id,{"requirement_id":identifier,"title":name,"description":sections.get('功能描述',''),"requirement_type":"functional","section_path":hierarchy,"inputs":[sections.get('输入','')],"processing_rules":[sections.get('处理','')],"outputs":[sections.get('输出','')],"source_document":old.get('source_document',''),"source_chunk_id":old.get('source_block_id',''),"need_human_confirm":False,"retained":False})
        return {"updated":updated,"deleted":deleted,"restored":restored}

    def submit_requirements_for_generation(self,project_id:str)->Dict[str,int]:
        with self.manager.connections.transaction() as conn:
            eligible=[dict(x) for x in conn.execute("SELECT identifier FROM requirement_nodes n WHERE project_id=? AND testable=1 AND enabled=1 AND deleted_at IS NULL AND review_status='human_confirmed' AND EXISTS(SELECT 1 FROM requirement_indicators i WHERE i.project_id=n.project_id AND i.function_id=n.identifier)",(project_id,))]
            if not eligible: raise ValueError('没有已确认、启用且具有非空原子需求的最低功能')
            ids=[x['identifier'] for x in eligible]; marks=','.join('?'*len(ids))
            conn.execute(f"UPDATE requirement_nodes SET generation_approved=1 WHERE project_id=? AND identifier IN ({marks})",(project_id,*ids))
            conn.execute(f"UPDATE project_requirements SET retained=1 WHERE project_id=? AND requirement_id IN ({marks})",(project_id,*ids))
        return {"submitted":len(ids)}

    def reparse_single_requirement(self,project_id:str,node_id:str,progress_callback:Any=None)->Dict[str,Any]:
        with self.manager.connections.transaction() as conn:
            row=conn.execute("SELECT identifier FROM requirement_nodes WHERE project_id=? AND node_id=? AND testable=1 AND enabled=1 AND deleted_at IS NULL",(project_id,node_id)).fetchone()
            if not row: raise KeyError('当前项目中不存在可重解析的启用需求')
            conn.execute("DELETE FROM requirement_indicators WHERE project_id=? AND function_id=?",(project_id,row['identifier']))
            conn.execute("UPDATE requirement_nodes SET review_status='pending',generation_approved=0 WHERE project_id=? AND node_id=?",(project_id,node_id))
            conn.execute("UPDATE project_requirements SET retained=0 WHERE project_id=? AND requirement_id=?",(project_id,row['identifier']))
        return self.atomize_and_audit_requirements(project_id,progress_callback,resume=True)

    def analyze_offline_html(self, project_id: str, filename: str, content: bytes, include_elements: bool = True) -> Dict[str, Any]:
        from application.services.traceability_service import iter_offline_html_elements
        from infrastructure.database.json_codec import dumps_json
        started=time.monotonic()
        logger.info("Static HTML parse started project=%s file=%s size=%s",project_id,filename,len(content))
        try: page, elements = iter_offline_html_elements(content, filename)
        except Exception:
            logger.exception("Static HTML parse failed project=%s file=%s",project_id,filename); raise
        form_ids=set(); button_count=0; input_count=0; count=0; returned=[] if include_elements else None
        import re
        text=content.decode("utf-8",errors="replace")
        compressed=re.sub(r"<!--.*?-->"," ",text,flags=re.S); compressed=re.sub(r"<(script|style)\b[^>]*>.*?</\1>"," ",compressed,flags=re.I|re.S); compressed=re.sub(r"<[^>]+>"," ",compressed); compressed=re.sub(r"\s+"," ",compressed).strip()[:8000]
        resource_rows=[]
        for kind,pattern in (("javascript",r"<script\b[^>]*\bsrc\s*=\s*['\"]([^'\"]+)['\"]"),("stylesheet",r"<link\b[^>]*\bhref\s*=\s*['\"]([^'\"]+)['\"]")):
            for index,match in enumerate(re.finditer(pattern,text,re.I)):
                reference=match.group(1)
                if reference.lower().startswith("data:"): continue
                resource_rows.append((f"RES-{kind}-{index}",kind,reference,f"external reference; length={len(reference)}"))
        inline_js=sum(1 for x in re.finditer(r"<script\b(?![^>]*\bsrc=)[^>]*>",text,re.I)); inline_css=sum(1 for x in re.finditer(r"<style\b[^>]*>",text,re.I))
        if inline_js: resource_rows.append(("RES-inline-js","javascript_inline","",f"inline blocks={inline_js}; content not persisted"))
        if inline_css: resource_rows.append(("RES-inline-css","stylesheet_inline","",f"inline blocks={inline_css}; content not persisted"))
        del text
        with self.manager.connections.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO html_pages(project_id,page_id,title,page_path,source_asset) VALUES(?,?,?,?,?)",(project_id,page["page_id"],page["title"],page["path"],filename))
            conn.execute("INSERT OR REPLACE INTO html_page_summaries(project_id,page_id,summary_json,source_bytes,compressed_chars) VALUES(?,?,?,?,?)",(project_id,page["page_id"],dumps_json({"page_id":page["page_id"],"title":page["title"],"path":page["path"],"visible_text_summary":compressed}),len(content),len(compressed)))
            conn.execute("DELETE FROM html_page_resources WHERE project_id=? AND page_id=?",(project_id,page["page_id"]))
            for resource_id,kind,reference,summary in resource_rows:
                conn.execute("INSERT INTO html_page_resources(project_id,page_id,resource_id,resource_type,reference,summary,missing) VALUES(?,?,?,?,?,?,0)",(project_id,page["page_id"],resource_id,kind,reference[:1000],summary[:200]))
            for item in elements:
                conn.execute("INSERT OR REPLACE INTO html_elements(project_id,element_id,page_id,tag,element_type,element_json) VALUES(?,?,?,?,?,?)",(project_id,item.element_id,item.page_id,item.tag,item.element_type,dumps_json(item.model_dump(mode="json"))))
                if returned is not None: returned.append(item.model_dump(mode="json"))
                count+=1
                if item.form_id: form_ids.add(item.form_id)
                button_count+=int(item.tag=="button" or item.element_type in {"button","submit"})
                input_count+=int(item.tag in {"input","textarea","select"})
        counts={"element_count":count,"form_count":len(form_ids),"button_count":button_count,"input_count":input_count}
        warnings=[]
        if len(content)>2*1024*1024: warnings.append("大文件已采用静态流式事件解析；完整DOM不会进入会话状态。")
        result={"page":page,"page_count":1,**counts,"resource_reference_count":len(resource_rows),"warnings":warnings,"elapsed_seconds":round(time.monotonic()-started,3)}
        if returned is not None: result["elements"]=returned
        logger.info("Static HTML parse completed project=%s elements=%s elapsed=%s",project_id,count,result["elapsed_seconds"])
        return result

    def analyze_site_zip(self, project_id: str, filename: str, content: bytes) -> Dict[str, Any]:
        from hashlib import sha256
        from application.services.offline_site_service import analyze_site, safe_extract_zip
        from application.services.traceability_service import parse_offline_html
        from infrastructure.database.json_codec import dumps_json
        site_id="SITE-"+sha256(content).hexdigest()[:16].upper(); root=self.manager.project_dir(project_id)/"site_packages"/site_id
        safe_extract_zip(content,root); manifest=analyze_site(root); entry=(manifest["entry_candidates"] or [""])[0]
        with self.manager.connections.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO site_packages(project_id,site_package_id,filename,root_path,entry_path,manifest_json) VALUES(?,?,?,?,?,?)",(project_id,site_id,filename,str(root),entry,dumps_json(manifest)))
            for page_data in manifest["pages"]:
                page,elements=parse_offline_html((root/page_data["path"]).read_bytes(),page_data["path"])
                conn.execute("INSERT OR REPLACE INTO html_pages(project_id,page_id,title,page_path,source_asset,site_package_id) VALUES(?,?,?,?,?,?)",(project_id,page["page_id"],page["title"],page["path"],filename,site_id))
                conn.execute("INSERT OR REPLACE INTO html_page_summaries(project_id,page_id,summary_json,source_bytes,compressed_chars) VALUES(?,?,?,?,?)",(project_id,page["page_id"],dumps_json({"page_id":page["page_id"],"title":page["title"],"path":page["path"],"visible_text_summary":page_data.get("visible_text_summary","")}),int(page_data.get("source_bytes",0)),int(page_data.get("compressed_chars",0))))
                for item in elements:
                    conn.execute("INSERT OR REPLACE INTO html_elements(project_id,element_id,page_id,tag,element_type,element_json,site_package_id) VALUES(?,?,?,?,?,?,?)",(project_id,item.element_id,item.page_id,item.tag,item.element_type,dumps_json(item.model_dump(mode="json")),site_id))
            for index,relation in enumerate(manifest["navigation_relations"]):
                conn.execute("INSERT OR REPLACE INTO site_navigation_relations(project_id,site_package_id,relation_id,source_page,target,relation_type,relation_json) VALUES(?,?,?,?,?,?,?)",(project_id,site_id,f"REL-{index:05d}",relation["source"],relation["target"],relation["kind"],dumps_json(relation)))
        return {"site_package_id":site_id,"root_path":str(root),**manifest}

    def explore_site_package(self, project_id: str, site_package_id: str, entry: str, plan: List[Dict[str, Any]]) -> Dict[str, Any]:
        from infrastructure.database.json_codec import dumps_json
        import json, os, subprocess, sys, uuid
        with self.manager.connections.connection() as conn:
            row=conn.execute("SELECT root_path FROM site_packages WHERE project_id=? AND site_package_id=?",(project_id,site_package_id)).fetchone()
        if not row: raise KeyError("当前项目中不存在该站点包")
        evidence=self.manager.project_dir(project_id)/"site_packages"/site_package_id/"evidence"; evidence.mkdir(parents=True,exist_ok=True)
        job_id=uuid.uuid4().hex[:12]; request_path=evidence/f"playwright-{job_id}-request.json"; output_path=evidence/f"playwright-{job_id}-result.json"; log_path=evidence/"playwright.log"
        request_path.write_text(json.dumps({"root":row["root_path"],"entry":entry,"plan":plan,"evidence_dir":str(evidence)},ensure_ascii=False),"utf-8")
        worker=Path(__file__).resolve().parents[2]/"scripts"/"playwright_worker.py"; env=os.environ.copy(); env["PYTHONPATH"]=str(Path(__file__).resolve().parents[2])
        try:
            with log_path.open("a",encoding="utf-8") as log:
                completed=subprocess.run([sys.executable,"-X","faulthandler",str(worker),"--request",str(request_path),"--output",str(output_path)],cwd=str(worker.parent.parent),env=env,stdout=log,stderr=subprocess.STDOUT,text=True,timeout=120,check=False)
        except subprocess.TimeoutExpired as exc:
            logger.exception("Playwright subprocess timeout project=%s site=%s",project_id,site_package_id)
            return {"entry":entry,"events":[],"blocked_requests":[],"return_code":-1,"failure_reason":f"TimeoutExpired: {exc}","playwright_log":str(log_path)}
        payload=json.loads(output_path.read_text("utf-8")) if output_path.exists() else {"ok":False,"error":"worker produced no result"}
        if completed.returncode or not payload.get("ok"):
            reason=payload.get("error") or f"worker return code {completed.returncode}"
            logger.error("Playwright subprocess failed project=%s site=%s reason=%s",project_id,site_package_id,reason)
            return {"entry":entry,"events":[],"blocked_requests":[],"return_code":completed.returncode,"failure_reason":reason,"playwright_log":str(log_path)}
        full_result=payload["result"]; compact_events=[]
        for event in full_result.get("events",[]):
            compact_events.append({k:v for k,v in event.items() if k not in {"before_dom_summary","after_dom_summary","visible_text_before","visible_text_after","controls"}})
        result={**full_result,"events":compact_events,"visible_text":str(full_result.get("visible_text", ""))[:5000],"return_code":completed.returncode,"failure_reason":"","playwright_log":str(log_path),"full_evidence_file":str(output_path)}
        with self.manager.connections.transaction() as conn:
            conn.execute("INSERT INTO html_observations(project_id,observation_id,page_id,action,result_json,site_package_id,evidence_json) VALUES(?,?,?,?,?,?,?)",(project_id,f"OBS-{__import__('uuid').uuid4().hex[:16]}",entry,"bounded_plan",dumps_json(result),site_package_id,dumps_json({"screenshots":[x.get("after_screenshot") for x in result["events"] if x.get("after_screenshot")]})))
        return result

    def playwright_status(self) -> Dict[str, Any]:
        from application.services.offline_site_service import playwright_health
        return playwright_health(Path(__file__).resolve().parents[2] / "vendor" / "playwright-browsers")

    def auto_explore_site_package(self, project_id: str, site_package_id: str, entry: str) -> Dict[str, Any]:
        from application.services.offline_site_service import automatic_safe_plan
        from urllib.parse import urlparse
        with self.manager.connections.connection() as conn:
            row=conn.execute("SELECT root_path FROM site_packages WHERE project_id=? AND site_package_id=?",(project_id,site_package_id)).fetchone()
        if not row: raise KeyError("当前项目中不存在该站点包")
        plan=automatic_safe_plan(Path(row["root_path"]),entry)
        if not plan: return {"entry":entry,"events":[],"status":"needs_human_confirmation","reason":"未找到唯一且安全的语义操作目标"}
        result=self.explore_site_package(project_id,site_package_id,entry,plan); result["semantic_plan_size"]=len(plan)
        next_entry=urlparse(result.get("final_url","")).path.lstrip("/")
        if next_entry and next_entry!=entry and (Path(row["root_path"])/next_entry).is_file():
            next_plan=automatic_safe_plan(Path(row["root_path"]),next_entry)
            if next_plan:
                second=self.explore_site_package(project_id,site_package_id,next_entry,next_plan)
                result["events"].extend(second.get("events",[])); result["blocked_requests"].extend(second.get("blocked_requests",[])); result["final_url"]=second.get("final_url",result.get("final_url")); result["semantic_plan_size"]+=len(next_plan)
        return result

    def auto_bind_requirements(self, project_id: str) -> Dict[str, Any]:
        from application.services.semantic_binding_service import bind_project
        visual=self.understand_pages_visually(project_id)
        result=bind_project(self.manager,project_id,self.settings); result["visual_understanding"]=visual; return result

    def binding_funnel(self,project_id:str)->Dict[str,Any]:
        with self.manager.connections.connection() as conn:
            one=lambda sql:conn.execute(sql,(project_id,)).fetchone()[0]
            return {"最低可测功能":one("SELECT count(*) FROM requirement_nodes WHERE project_id=? AND testable=1 AND enabled=1 AND deleted_at IS NULL"),"原子需求":one("SELECT count(*) FROM requirement_indicators WHERE project_id=?"),"HTML页面":one("SELECT count(*) FROM html_pages WHERE project_id=?"),"可交互元素":one("SELECT count(*) FROM html_elements WHERE project_id=?"),"Playwright观测":one("SELECT count(*) FROM html_observations WHERE project_id=? AND action='bounded_plan'"),"视觉理解状态":"已完成" if one("SELECT count(*) FROM html_observations WHERE project_id=? AND action='page_understanding'") else "未执行","待绑定功能":one("SELECT count(*) FROM requirement_nodes WHERE project_id=? AND testable=1 AND enabled=1 AND deleted_at IS NULL AND identifier NOT IN (SELECT function_id FROM requirement_page_links WHERE project_id=requirement_nodes.project_id)")}

    def understand_pages_visually(self, project_id: str) -> Dict[str, Any]:
        from infrastructure.database.json_codec import dumps_json, loads_json
        client=OllamaVisualClient(base_url=self.settings.ollama_base_url,model=self.settings.page_understanding_model,timeout=self.settings.ollama_vision_timeout)
        with self.manager.connections.connection() as conn:
            observations=[dict(x) for x in conn.execute("SELECT * FROM html_observations WHERE project_id=? ORDER BY observed_at DESC",(project_id,))]
            nodes=[dict(x) for x in conn.execute("SELECT identifier,name,hierarchy_path_json,sections_json FROM requirement_nodes WHERE project_id=? AND testable=1",(project_id,))]
        screenshots=[]
        for row in observations:
            evidence=loads_json(row.get("evidence_json"),{}); screenshots.extend(x for x in evidence.get("screenshots",[]) if x and Path(x).is_file())
        if not screenshots: return {"status":"no_screenshot","model":self.settings.page_understanding_model,"results":[]}
        prompt="分析离线页面截图。结合需求、可见页面结构，输出JSON：page_purpose、regions、function_candidates、control_candidates、confidence、reason、next_safe_targets。视觉仅作辅助，不得编造DOM元素。需求："+dumps_json([{"identifier":x["identifier"],"name":x["name"],"hierarchy":loads_json(x["hierarchy_path_json"],[]),"sections":loads_json(x["sections_json"],{})} for x in nodes])
        response=client.generate_json_with_images(prompt,image_paths=[screenshots[-1]])
        if not response.get("ok"): return {"status":"failed","model":self.settings.page_understanding_model,"error":response.get("error","")}
        data=response.get("data") or {}; observation_id=f"VIS-{__import__('uuid').uuid4().hex[:16]}"
        with self.manager.connections.transaction() as conn:
            conn.execute("INSERT INTO html_observations(project_id,observation_id,page_id,action,result_json,evidence_json) VALUES(?,?,?,?,?,?)",(project_id,observation_id,"","page_understanding",dumps_json({"model":self.settings.page_understanding_model,"visual_result":data,"expected_source":"html_observed"}),dumps_json({"screenshot":screenshots[-1]})))
        return {"status":"completed","model":self.settings.page_understanding_model,"observation_id":observation_id,"result":data}

    def workflow_status(self, project_id: str) -> Dict[str, Any]:
        with self.manager.connections.connection() as conn:
            scalar=lambda sql: conn.execute(sql,(project_id,)).fetchone()[0]
            nodes=scalar("SELECT count(*) FROM requirement_nodes WHERE project_id=?")
            testable=scalar("SELECT count(*) FROM requirement_nodes WHERE project_id=? AND testable=1")
            atoms=scalar("SELECT count(*) FROM requirement_indicators WHERE project_id=?")
            reviewed=scalar("SELECT count(*) FROM requirement_nodes WHERE project_id=? AND testable=1 AND review_status IN ('passed','human_confirmed')")
            pages=scalar("SELECT count(*) FROM html_pages WHERE project_id=?")
            observations=scalar("SELECT count(*) FROM html_observations WHERE project_id=?")
            bindings=scalar("SELECT count(*) FROM requirement_page_links WHERE project_id=? AND status='confirmed'")
            pending=scalar("SELECT count(*) FROM requirement_page_links WHERE project_id=? AND need_human_confirm=1")+scalar("SELECT count(*) FROM requirement_element_links WHERE project_id=? AND need_human_confirm=1")
            covered=scalar("SELECT count(DISTINCT indicator_id) FROM case_indicator_links WHERE project_id=?")
            online=scalar("SELECT count(*) FROM requirement_indicators WHERE project_id=? AND verification_scope='online_required'")
            packages=scalar("SELECT count(*) FROM site_packages WHERE project_id=?")
        cases=len(self.manager.list_generated_cases(project_id)); total=max(1,atoms)
        return {"document_uploaded":bool(self.manager.list_documents(project_id)),"section_32_identified":bool(nodes),"section_33_identified":bool(nodes),"lowest_function_count":testable,"atom_count":atoms,"atom_review_passed":bool(testable and reviewed==testable),"html_uploaded":bool(packages or pages),"page_count":pages,"playwright":self.playwright_status(),"exploration_completed":bool(observations),"binding_completion":round(bindings/max(1,testable),4),"pending_confirmation":pending,"case_count":cases,"atomic_coverage_rate":round(covered/total,4),"online_required":online,"reviewed":bool(self.manager.list_review_results(project_id)),"exported":any((self.manager.project_dir(project_id)/"exports").iterdir()) if (self.manager.project_dir(project_id)/"exports").exists() else False}

    def traceability_rows(self, project_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """Return bounded UI rows; full datasets are read only by explicit export."""
        queries={
            "requirement_hierarchy":"SELECT identifier,name,level,node_type,testable,review_status FROM requirement_nodes WHERE project_id=? ORDER BY source_block_id LIMIT 500",
            "atomic_requirements":"SELECT indicator_id,function_id,indicator_text,indicator_type,verification_scope,need_human_confirm FROM requirement_indicators WHERE project_id=? ORDER BY function_id,indicator_id LIMIT 500",
            "requirement_page_links":"SELECT link_id,'page' AS binding_type,function_id,page_id,'' AS confirmed_element_id,confidence,reason,status,need_human_confirm FROM requirement_page_links WHERE project_id=? ORDER BY function_id LIMIT 500",
            "requirement_element_links":"SELECT link_id,'element' AS binding_type,indicator_id,page_id,confirmed_element_id,confidence,reason,status,need_human_confirm FROM requirement_element_links WHERE project_id=? ORDER BY indicator_id LIMIT 500",
            "atomic_coverage_matrix":"SELECT indicator_id,case_id,case_version,coverage_type,coverage_status FROM case_indicator_links WHERE project_id=? ORDER BY indicator_id,case_id LIMIT 500",
            "case_version_history":"SELECT case_id,version_no,parent_version_no,acceptance_status,operator,created_at FROM case_versions WHERE project_id=? ORDER BY case_id,version_no LIMIT 500",
        }
        with self.manager.connections.connection() as conn:
            return {key:[dict(row) for row in conn.execute(sql,(project_id,))] for key,sql in queries.items()}

    def list_html_elements_page(self, project_id: str, page: int = 1, page_size: int = 100) -> Dict[str, Any]:
        page_size=max(1,min(int(page_size),100)); page=max(1,int(page)); offset=(page-1)*page_size
        with self.manager.connections.connection() as conn:
            total=conn.execute("SELECT count(*) FROM html_elements WHERE project_id=?",(project_id,)).fetchone()[0]
            rows=[dict(row) for row in conn.execute("SELECT page_id,element_id,tag,element_type FROM html_elements WHERE project_id=? ORDER BY page_id,element_id LIMIT ? OFFSET ?",(project_id,page_size,offset))]
        return {"rows":rows,"total":total,"page":page,"page_size":page_size,"pages":max(1,(total+page_size-1)//page_size)}

    def case_regeneration_context(self, project_id: str, case_id: str) -> Dict[str, Any]:
        from application.services.case_regeneration_service import CaseRegenerationService
        return CaseRegenerationService(self.manager, self.settings).context(project_id, case_id)

    def regenerate_single_case(self, project_id: str, case_id: str, feedback: str) -> Dict[str, Any]:
        from application.services.case_regeneration_service import CaseRegenerationService
        return CaseRegenerationService(self.manager, self.settings).regenerate(project_id, case_id, feedback)

    def update_case_version(self, project_id: str, case_id: str, version_no: int, action: str) -> Dict[str, Any]:
        from infrastructure.repositories.traceability_repository import TraceabilityRepository
        repository=TraceabilityRepository(self.manager.connections)
        if action=="accept": return repository.accept_version(project_id,case_id,version_no)
        if action=="reject": repository.set_version_status(project_id,case_id,version_no,"rejected"); return {"status":"rejected"}
        if action=="rollback": return repository.rollback(project_id,case_id,version_no,operator="streamlit-user")
        raise ValueError("不支持的版本操作")

    def export_excel(self, project_id: str) -> Path:
        return export_project_excel(self.manager, project_id)

    def export_word(self, project_id: str) -> Path:
        return export_project_word(self.manager, project_id)

    def export_markdown(self, project_id: str) -> Path:
        return export_project_markdown(self.manager, project_id)
