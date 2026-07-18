"""Legacy project case generator delegating to GenerationService."""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.ollama_client import OllamaClient
from core.project_manager import ProjectManager
from services.generation_service import GenerationRequest, GenerationService


CASE_TYPE_LABELS = {
    "功能测试": "功能测试",
    "性能测试": "性能测试",
    "接口测试": "接口测试",
    "异常测试": "异常测试",
    "安全性": "安全性",
    "可靠性": "可靠性",
    "维修性": "维修性",
    "保障性": "保障性",
    "测试性": "测试性",
    "环境适应性": "环境适应性",
    "六性测试": "六性测试",
}


class ProjectCaseGenerator:
    """Compatibility interface for the retired project-pages workflow."""

    def __init__(
        self,
        manager: ProjectManager,
        ollama: Optional[OllamaClient] = None,
        case_library: Optional[Any] = None,
    ) -> None:
        self.manager = manager
        self.ollama = ollama
        self.case_library = case_library

    def generate(
        self,
        project_id: str,
        requirement_id: str,
        case_type: str,
        top_k_chunks: int = 5,
        top_k_history: int = 5,
        use_ollama: bool = True,
        use_project_kb: bool = True,
        use_history: bool = True,
    ) -> Dict[str, Any]:
        result = GenerationService(self.manager, self.case_library).generate_test_cases(
            GenerationRequest(
                project_id=project_id,
                requirement_ids=[requirement_id],
                case_type=CASE_TYPE_LABELS.get(case_type, case_type),
                case_count=1,
                requested_mode="auto" if use_ollama else "rule",
                use_project_kb=use_project_kb,
                use_history=use_history,
                top_k_chunks=top_k_chunks,
                top_k_history=top_k_history,
            )
        )
        data = dict(result.cases[0].persistence_data)
        source_ids = set(data.get("source_chunk_ids") or [])
        data["_source_chunks"] = [
            row
            for row in self.manager.list_chunks(project_id, limit=5000)
            if str(row.get("chunk_id") or "") in source_ids
        ]
        data["generation_mode"] = result.generation_mode
        data["fallback_reason"] = result.fallback_reason
        return data
