"""Deprecated project-bound compatibility wrapper for test-case generation."""
from __future__ import annotations

import warnings
from typing import Any, List, Optional

from core.case_library import CaseLibraryManager
from models.schemas import RequirementItem, TestCaseItem
from services.generation_service import GenerationRequest


class TestCaseGenerator:
    """Delegate the legacy interface to the project-scoped GenerationService."""

    def __init__(
        self,
        ollama: Optional[Any] = None,
        generation_service: Optional[Any] = None,
        project_id: str = "",
    ) -> None:
        self.ollama = ollama
        self.generation_service = generation_service
        self.project_id = project_id

    def generate(
        self,
        req: RequirementItem,
        six_categories: List[str],
        library: Optional[CaseLibraryManager],
        top_k: int,
        use_ollama: bool,
        case_seq: int = 1,
        reference_standards: str = "GJB/Z 141、GJB 438C、GJB 5000B、GJB 9001C",
        template_type: str = "测试用例",
        global_scenario: str = "",
        writing_standard: str = "",
    ) -> TestCaseItem:
        """Generate through the bound service; unscoped generation is forbidden."""
        del six_categories, case_seq, reference_standards, template_type, global_scenario, writing_standard
        warnings.warn(
            "TestCaseGenerator is deprecated; use services.GenerationService",
            DeprecationWarning,
            stacklevel=2,
        )
        if self.generation_service is None or not self.project_id:
            raise RuntimeError(
                "Legacy TestCaseGenerator requires generation_service and a bound project_id"
            )
        result = self.generation_service.generate_test_cases(
            GenerationRequest(
                project_id=self.project_id,
                requirement_ids=[req.requirement_id],
                case_type="功能测试",
                case_count=1,
                requested_mode="auto" if use_ollama else "rule",
                use_history=library is not None,
                top_k_history=top_k,
            )
        )
        return TestCaseItem(**result.cases[0].persistence_data)
