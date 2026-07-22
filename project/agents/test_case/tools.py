"""Read-only, project-bound LangChain tools for test-case generation."""

from __future__ import annotations

from typing import Any, Dict, List

from langchain_core.tools import BaseTool, tool

from agents.test_case.context import AgentRuntimeContext
from domain.rules.quality_classifier import classify_requirement
from domain.rules.test_method import match_test_methods
from domain.exceptions import PersistenceError, RetrievalError
from infrastructure.equipment.equipment_service import EquipmentService
from infrastructure.database.json_codec import loads_json
from workflows.learning.knowledge_review_service import KnowledgeReviewService
from domain.schemas.generation import RequirementItem


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
            # Scenario-only requests legitimately have no requirement ID. A
            # local model may still probe this tool with an empty or guessed
            # value; expose the miss without aborting the complete Agent run.
            return {
                "found": False,
                "requirement_id": requirement_id,
                "notice": "当前项目未找到该需求；请仅使用已编译场景和实际检索到的来源。",
                "fact_source": "current_project_requirement_lookup",
            }
        context.record_chunks([str(row.get("source_chunk_id") or "")])
        context.record_documents([str(row.get("source_document") or "")])
        return {
            "found": True,
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
            return {
                "found": False,
                "requirement_id": requirement_id,
                "quality_categories": [],
                "recommended_methods": [],
                "notice": "当前项目未找到该需求，不能提供需求级测试方法建议。",
                "guidance_source": "deterministic_project_rules",
            }
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
            "found": True,
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

    def approved_knowledge(
        query: str = "", knowledge_type: str = "", allow_global: bool = False,
        record: bool = True,
    ) -> List[Dict[str, Any]]:
        rows = KnowledgeReviewService(context.manager).query_for_scenario(
            context.project_id, include_global=allow_global
        )
        tokens = [item.casefold() for item in query.split() if item]
        result = []
        for row in rows:
            if row.get("status") != "approved":
                continue
            if knowledge_type and row.get("knowledge_type") != knowledge_type:
                continue
            text = f"{row.get('title', '')} {row.get('content', '')}".casefold()
            if tokens and not all(item in text for item in tokens):
                continue
            result.append({
                "knowledge_unit_id": row["knowledge_unit_id"],
                "knowledge_type": row["knowledge_type"], "title": row.get("title", ""),
                "content": row.get("content", ""),
                "normalized_data": row.get("normalized_data", {}),
                "applicable_conditions": row.get("applicable_conditions", []),
                "inapplicable_conditions": row.get("inapplicable_conditions", []),
                "section_scope": row.get("section_scope", ""),
                "project_id": row.get("project_id", ""),
                "document_id": row.get("document_id", ""),
                "chunk_id": row.get("chunk_id", ""), "page_no": row.get("page_no"),
                "fact_source": "approved_knowledge",
            })
        if record:
            context.record_knowledge([str(row["knowledge_unit_id"]) for row in result])
            context.record_chunks([str(row.get("chunk_id") or "") for row in result])
        return result

    @tool
    def search_approved_knowledge(
        query: str, knowledge_type: str = "", allow_global: bool = False
    ) -> List[Dict[str, Any]]:
        """Search approved knowledge in the bound project; GLOBAL requires allow_global=true."""
        context.record_tool("search_approved_knowledge")
        return approved_knowledge(query, knowledge_type, allow_global)

    @tool
    def get_simulation_model(query: str = "", allow_global: bool = False) -> List[Dict[str, Any]]:
        """Return approved simulation-model knowledge with conditions and source traces."""
        context.record_tool("get_simulation_model")
        return approved_knowledge(query, "simulation_model", allow_global)

    @tool
    def get_state_transitions(query: str = "", allow_global: bool = False) -> List[Dict[str, Any]]:
        """Return approved state transitions from the bound scope."""
        context.record_tool("get_state_transitions")
        return approved_knowledge(query, "state_transition", allow_global)

    @tool
    def get_verified_parameters(query: str = "", allow_global: bool = False) -> List[Dict[str, Any]]:
        """Return approved parameters that retain unit, operating condition, and source."""
        context.record_tool("get_verified_parameters")
        rows = approved_knowledge(query, "parameter", allow_global, record=False)
        result = [
            row for row in rows
            if row.get("normalized_data", {}).get("unit")
            and row.get("normalized_data", {}).get("operating_condition")
            and (row.get("chunk_id") or row.get("document_id"))
        ]
        context.record_knowledge([str(row["knowledge_unit_id"]) for row in result])
        context.record_chunks([str(row.get("chunk_id") or "") for row in result])
        return result

    @tool
    def search_scenario_templates(query: str = "", allow_global: bool = False) -> List[Dict[str, Any]]:
        """Search approved scenario templates; GLOBAL access is explicit."""
        context.record_tool("search_scenario_templates")
        scopes = [context.project_id, *(["GLOBAL"] if allow_global else [])]
        placeholders = ",".join("?" for _ in scopes)
        with context.manager.connections.connection() as conn:
            rows = [dict(row) for row in conn.execute(
                f"""SELECT * FROM scenario_templates WHERE project_id IN ({placeholders})
                AND status='approved' ORDER BY CASE WHEN project_id=? THEN 0 ELSE 1 END,name""",
                (*scopes, context.project_id),
            )]
        needle = query.casefold().strip()
        result = []
        for row in rows:
            payload = loads_json(row.get("template_json"), {})
            if needle and needle not in f"{row.get('name', '')} {row.get('scenario_category', '')} {payload}".casefold():
                continue
            result.append({
                "template_id": row["template_id"], "name": row["name"],
                "scenario_category": row.get("scenario_category", ""), "template": payload,
                "project_id": row["project_id"], "document_id": row.get("document_id", ""),
                "chunk_id": row.get("chunk_id", ""), "page_no": row.get("page_no"),
                "fact_source": "approved_scenario_template",
            })
        context.record_chunks([str(row.get("chunk_id") or "") for row in result])
        return result

    @tool
    def search_equipment_candidates(
        query: str = "", role: str = "", capabilities: List[str] | None = None,
        allow_global: bool = False, limit: int = 20,
    ) -> Dict[str, Any]:
        """Return deterministic equipment candidates, never an approved allocation decision."""
        context.record_tool("search_equipment_candidates")
        result = EquipmentService(context.manager.equipment).search(
            context.project_id, name=query, roles=[role] if role else [],
            capabilities=capabilities or [], allow_global=allow_global,
            limit=min(max(limit, 1), 50),
        )
        payload = result.model_dump(mode="json")
        context.record_equipment([item["equipment_id"] for item in payload["matches"]])
        context.record_source_refs([
            ref for item in [*payload["matches"], *payload["rejected_matches"]]
            for ref in item.get("source_refs", [])
        ])
        payload["notice"] = "候选装备不是已批准配置，必须读取场景装备分配结果"
        return payload

    @tool
    def get_scenario_equipment_allocation(scenario_id: str) -> List[Dict[str, Any]]:
        """Return persisted equipment allocations for one bound-project compiled scenario."""
        context.record_tool("get_scenario_equipment_allocation")
        with context.manager.connections.connection() as conn:
            if not conn.execute(
                "SELECT 1 FROM scenario_cards WHERE project_id=? AND scenario_id=?",
                (context.project_id, scenario_id),
            ).fetchone():
                return []
            rows = [dict(row) for row in conn.execute(
                """SELECT * FROM scenario_equipment_allocations
                WHERE project_id=? AND scenario_id=? ORDER BY role_requirement_id,equipment_id""",
                (context.project_id, scenario_id),
            )]
        result = []
        for row in rows:
            item = {
                "allocation_id": row["allocation_id"], "scenario_id": row["scenario_id"],
                "role_requirement_id": row["role_requirement_id"],
                "equipment_id": row["equipment_id"], "quantity": row["quantity"],
                "configuration": loads_json(row.get("configuration_json"), {}),
                "capability_ids": loads_json(row.get("capability_ids_json"), []),
                "configuration_rule_ids": loads_json(row.get("rule_ids_json"), []),
                "source_refs": loads_json(row.get("source_refs_json"), []),
                "need_human_confirm": bool(row.get("need_human_confirm")),
                "fact_source": "compiled_scenario_allocation",
            }
            result.append(item)
        context.record_scenarios([scenario_id])
        context.record_equipment([row["equipment_id"] for row in result])
        context.record_rules([rule for row in result for rule in row["configuration_rule_ids"]])
        context.record_source_refs([ref for row in result for ref in row["source_refs"]])
        return result

    @tool
    def get_scenario_validation_result(scenario_id: str) -> Dict[str, Any]:
        """Return the latest validation result for a bound-project compiled scenario."""
        context.record_tool("get_scenario_validation_result")
        with context.manager.connections.connection() as conn:
            row = conn.execute(
                """SELECT * FROM scenario_validation_results WHERE project_id=? AND scenario_id=?
                ORDER BY created_at DESC,validation_id DESC LIMIT 1""",
                (context.project_id, scenario_id),
            ).fetchone()
        if not row:
            return {}
        data = dict(row)
        result = {
            "scenario_validation_run_id": data["validation_id"],
            "scenario_id": data["scenario_id"], "passed": bool(data.get("passed", data["is_valid"])),
            "score": float(data.get("score") or 0),
            "blocking_issues": loads_json(data.get("blocking_issues_json"), []),
            "warnings": loads_json(data.get("warnings_json"), []),
            "missing_information": loads_json(data.get("missing_information_json"), []),
            "check_results": loads_json(data.get("check_results_json"), {}),
            "need_human_confirm": bool(data.get("need_human_confirm")),
            "fact_source": "scenario_validation_result",
        }
        context.record_scenarios([scenario_id])
        context.record_validation_runs([data["validation_id"]])
        return result

    @tool
    def get_compiled_scenario(scenario_id: str) -> Dict[str, Any]:
        """Return one approved compiled scenario from the bound project."""
        context.record_tool("get_compiled_scenario")
        row = next(
            (item for item in context.manager.scenarios.list_compiled(
                context.project_id, "approved"
            ) if item.get("scenario_id") == scenario_id),
            None,
        )
        if not row:
            return {}
        context.record_scenarios([scenario_id])
        context.record_chunks([str(item) for item in row.get("source_chunk_ids", [])])
        context.record_knowledge([str(item) for item in row.get("knowledge_unit_ids", [])])
        context.record_equipment([
            str(item.get("equipment_id") or "")
            for item in row.get("equipment_allocations", [])
        ])
        context.record_rules([
            str(rule_id)
            for item in row.get("equipment_allocations", [])
            for rule_id in item.get("rule_ids", [])
        ])
        return {**row, "fact_source": "approved_compiled_scenario"}

    @tool
    def get_approved_feedback_rules(rule_type: str = "", allow_global: bool = False) -> List[Dict[str, Any]]:
        """Return approved deterministic feedback rules from the bound scope."""
        context.record_tool("get_approved_feedback_rules")
        scopes = [context.project_id, *(["GLOBAL"] if allow_global else [])]
        placeholders = ",".join("?" for _ in scopes)
        query = f"SELECT * FROM approved_learning_rules WHERE project_id IN ({placeholders}) AND enabled=1"
        params: List[Any] = list(scopes)
        if rule_type:
            query += " AND rule_type=?"
            params.append(rule_type)
        query += " ORDER BY CASE WHEN project_id=? THEN 0 ELSE 1 END,approved_at,learning_rule_id"
        params.append(context.project_id)
        with context.manager.connections.connection() as conn:
            rows = [dict(row) for row in conn.execute(query, tuple(params))]
        result = [{
            "configuration_rule_id": row["learning_rule_id"], "name": row["name"],
            "rule_type": row["rule_type"], "condition": loads_json(row.get("condition_json"), {}),
            "action": loads_json(row.get("action_json"), {}), "project_id": row["project_id"],
            "document_id": row.get("document_id", ""), "chunk_id": row.get("chunk_id", ""),
            "page_no": row.get("page_no"), "approved_by": row.get("approved_by", ""),
            "fact_source": "approved_feedback_rule",
        } for row in rows]
        context.record_rules([row["configuration_rule_id"] for row in result])
        context.record_chunks([str(row.get("chunk_id") or "") for row in result])
        return result

    return [
        get_project_profile,
        get_requirement_context,
        search_project_documents,
        get_related_scenarios,
        get_test_method_guidance,
        get_reference_cases,
        search_approved_knowledge,
        get_simulation_model,
        get_state_transitions,
        get_verified_parameters,
        search_scenario_templates,
        search_equipment_candidates,
        get_scenario_equipment_allocation,
        get_scenario_validation_result,
        get_compiled_scenario,
        get_approved_feedback_rules,
    ]
