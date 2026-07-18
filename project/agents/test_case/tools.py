"""Read-only, project-bound LangChain tools for test-case generation."""

from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.tools import BaseTool, tool

from agents.test_case.context import AgentRuntimeContext
from core.six_quality_classifier import classify_requirement
from core.test_method_matcher import match_test_methods
from domain.exceptions import PersistenceError, RetrievalError
from models.schemas import RequirementItem


def build_test_case_tools(context: AgentRuntimeContext) -> List[BaseTool]:
    """Build tools whose closures make arbitrary project selection impossible."""

    @tool
    def get_project_profile() -> Dict[str, Any]:
        """Return the bound current project's concise profile and documented constraints."""
        context.record_tool("get_project_profile")
        try:
            profile = context.manager.get_profile(context.project_id) or {}
            return {
                "project_id": context.project_id,
                "project_name": profile.get("project_name", ""),
                "domain": profile.get("domain", ""),
                "test_object": profile.get("test_object", ""),
                "main_functions": list(profile.get("main_functions") or []),
                "interfaces": list(profile.get("interfaces") or []),
                "constraints": list(profile.get("constraints") or []),
                "fact_source": "current_project_profile",
            }
        except (ValueError, TypeError, OSError) as exc:
            raise PersistenceError(
                f"get_project_profile failed for bound project: {exc!r}"
            ) from exc

    @tool
    def get_requirement_context(requirement_id: str) -> Dict[str, Any]:
        """Return one requirement from the bound project with its source trace."""
        context.record_tool("get_requirement_context")
        try:
            row = context.manager.get_requirement(context.project_id, requirement_id)
        except (ValueError, TypeError, OSError) as exc:
            raise PersistenceError(f"get_requirement_context failed: {exc!r}") from exc
        if not row:
            raise RetrievalError(
                f"Requirement {requirement_id!r} was not found in the bound project"
            )
        context.record_chunks([str(row.get("source_chunk_id") or "")])
        context.record_documents([str(row.get("source_document") or "")])
        return {
            "requirement_id": row.get("requirement_id", requirement_id),
            "title": row.get("title", ""),
            "description": row.get("description", ""),
            "category": row.get("category", ""),
            "source_document": row.get("source_document", ""),
            "source_chunk_id": row.get("source_chunk_id", ""),
            "fact_source": "current_project_requirement",
        }

    @tool
    def search_project_documents(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Search only bound-project documents; results include chunk and document trace IDs."""
        context.record_tool("search_project_documents")
        if context.retriever is None:
            raise RetrievalError("Project retriever is not configured")
        rows = context.retriever.search(
            query, min(max(top_k, 1), context.settings.top_k_cases)
        )
        context.record_chunks([row.chunk_id for row in rows])
        context.record_documents([row.document_name for row in rows])
        return [
            {
                "chunk_id": row.chunk_id,
                "document_id": row.document_id,
                "document_name": row.document_name,
                "content": row.content,
                "score": row.score,
                "retrieval_method": row.retrieval_method,
                "page_number": row.page_number,
                "fact_source": "current_project_document",
            }
            for row in rows
        ]

    @tool
    def get_related_scenarios(requirement_id: str) -> List[Dict[str, Any]]:
        """Return bound-project scenarios linked to a requirement with source chunk IDs."""
        context.record_tool("get_related_scenarios")
        try:
            rows = context.manager.list_scenario_cards(
                context.project_id, requirement_id
            )
        except (ValueError, TypeError, OSError) as exc:
            raise PersistenceError(f"get_related_scenarios failed: {exc!r}") from exc
        context.record_scenarios([str(row.get("scenario_id") or "") for row in rows])
        context.record_chunks(
            [
                str(chunk_id)
                for row in rows
                for chunk_id in (row.get("source_chunk_ids") or [])
            ]
        )
        return [
            {
                "scenario_id": row.get("scenario_id", ""),
                "scenario_name": row.get("scenario_name", ""),
                "preconditions": row.get("preconditions", []),
                "trigger_event": row.get("trigger_event", ""),
                "input_data": row.get("input_data", []),
                "system_state": row.get("system_state", ""),
                "external_interfaces": row.get("external_interfaces", []),
                "normal_flow": row.get("normal_flow", []),
                "abnormal_flow": row.get("abnormal_flow", []),
                "source_chunk_ids": row.get("source_chunk_ids", []),
                "need_human_confirm": bool(row.get("need_human_confirm", True)),
                "fact_source": "current_project_scenario",
            }
            for row in rows
        ]

    @tool
    def get_test_method_guidance(requirement_id: str) -> Dict[str, Any]:
        """Return deterministic six-quality classification and test-method guidance."""
        context.record_tool("get_test_method_guidance")
        row = context.manager.get_requirement(context.project_id, requirement_id)
        if not row:
            raise RetrievalError(
                f"Requirement {requirement_id!r} was not found in the bound project"
            )
        item = RequirementItem(
            requirement_id=requirement_id,
            requirement_text=str(row.get("description") or row.get("title") or ""),
            test_object=str(
                (context.manager.get_profile(context.project_id) or {}).get(
                    "test_object"
                )
                or ""
            ),
        )
        categories = classify_requirement(item, False, None)
        return {
            "requirement_id": requirement_id,
            "quality_categories": categories,
            "recommended_methods": match_test_methods(item, categories),
            "guidance_source": "deterministic_project_rules",
        }

    @tool
    def get_reference_cases(query: str, top_k: int = 3) -> Dict[str, Any]:
        """Return method/format references only; historical cases are never project facts."""
        context.record_tool("get_reference_cases")
        notice = "仅作测试方法和写作格式参考，不是当前项目事实，不得生成项目接口、阈值、状态或环境。"
        if context.case_library is None:
            return {"reference_only": True, "notice": notice, "cases": []}
        try:
            rows = context.case_library.search_similar_cases(
                query_text=query,
                top_k=min(max(top_k, 1), context.settings.top_k_cases),
            )
        except (ValueError, TypeError, OSError) as exc:
            raise RetrievalError(f"get_reference_cases failed: {exc!r}") from exc
        references = []
        for item, score, reason in rows:
            references.append(
                {
                    "reference_case_id": item.case_id,
                    "test_method": item.test_method,
                    "step_format": "编号步骤" if item.test_steps else "未提供",
                    "has_evaluation_criteria": bool(item.pass_criteria),
                    "similarity_score": score,
                    "reference_reason": reason,
                    "reference_only": True,
                }
            )
        return {"reference_only": True, "notice": notice, "cases": references}

    return [
        get_project_profile,
        get_requirement_context,
        search_project_documents,
        get_related_scenarios,
        get_test_method_guidance,
        get_reference_cases,
    ]
