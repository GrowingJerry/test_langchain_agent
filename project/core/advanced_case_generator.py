"""Compatibility facade delegating scenario-adapted generation to GenerationService."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.context_builder import ContextBuilder
from services.generation_service import GenerationRequest, GenerationService


class AdvancedCaseGenerator:
    """Legacy main-UI interface backed by the unified generation service."""

    def __init__(
        self,
        manager: Any,
        ollama: Optional[Any] = None,
        case_library: Optional[Any] = None,
    ):
        self.manager = manager
        self.ollama = ollama
        self.case_library = case_library
        self.context_builder = ContextBuilder(manager, case_library)

    def preview_context(
        self,
        project_id: str,
        requirement_id: str,
        case_type: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        return self.context_builder.build(
            project_id, requirement_id, case_type, persist=False, **kwargs
        )

    def generate(
        self,
        project_id: str,
        requirement_id: str,
        case_type: str,
        top_k_chunks: int = 5,
        top_k_history: int = 5,
        use_project_kb: bool = True,
        use_history: bool = True,
        use_ollama: bool = True,
    ) -> Dict[str, Any]:
        request = GenerationRequest(
            project_id=project_id,
            requirement_ids=[requirement_id],
            case_type=case_type,
            case_count=1,
            requested_mode="auto" if use_ollama else "rule",
            use_project_kb=use_project_kb,
            use_history=use_history,
            top_k_chunks=top_k_chunks,
            top_k_history=top_k_history,
        )
        result = GenerationService(self.manager, self.case_library).generate_test_cases(
            request
        )
        record = result.cases[0]
        context = self.preview_context(
            project_id,
            requirement_id,
            case_type,
            top_k_chunks=top_k_chunks,
            top_k_history=top_k_history,
            use_project_kb=use_project_kb,
            use_history=use_history,
        )
        return {
            "case": record.persistence_data,
            "context": context,
            "quality": record.quality,
            "generation_mode": result.generation_mode,
            "fallback_reason": result.fallback_reason,
            "generation_result": result.model_dump(mode="json"),
        }
