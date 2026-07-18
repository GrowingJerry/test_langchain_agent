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

    def __getattr__(self, name: str) -> Any:
        """Temporary façade forwarding while page-specific query services are extracted."""
        return getattr(self.manager, name)

    def ingest_document(
        self, project_id: str, filename: str, content: bytes
    ) -> Dict[str, object]:
        return self.uploads.ingest_document(project_id, filename, content)

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

    def review_project(self, project_id: str, include_llm: bool = False) -> List[Any]:
        return self.reviews.review_project(project_id, include_llm)

    def confirm_case_update(
        self, project_id: str, case_id: str, case: Dict[str, Any], run_id: str
    ) -> None:
        self.reviews.confirm_update(project_id, case_id, case, run_id)

    def export_rows(self, project_id: str) -> Dict[str, List[Dict[str, Any]]]:
        return build_project_export_rows(self.manager, project_id)

    def export_excel(self, project_id: str) -> Path:
        return export_project_excel(self.manager, project_id)

    def export_word(self, project_id: str) -> Path:
        return export_project_word(self.manager, project_id)

    def export_markdown(self, project_id: str) -> Path:
        return export_project_markdown(self.manager, project_id)
