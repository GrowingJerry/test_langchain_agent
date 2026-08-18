"""Build and persist grounded context for scenario-adapted case generation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from infrastructure.retrieval.project_knowledge import search_project_chunks
from domain.rules.quality_classifier import classify_requirement
from domain.rules.test_method import match_test_methods
from domain.schemas.generation import RequirementItem
from infrastructure.database.json_codec import loads_json


class ContextBuilder:
    def __init__(self, manager, case_library: Optional[Any] = None):
        self.manager = manager
        self.case_library = case_library

    @staticmethod
    def _evidence_requirement_ids(evidence: Dict[str, Any]) -> set[str]:
        """Normalize requirement bindings stored inside visual evidence JSON."""
        ids: set[str] = set()
        for key in ("requirement_id", "related_requirement_id"):
            value = str(evidence.get(key) or "").strip()
            if value:
                ids.add(value)
        related = evidence.get("related_requirement_ids") or []
        if isinstance(related, str):
            related = [related]
        if isinstance(related, list):
            ids.update(str(item).strip() for item in related if str(item).strip())
        return ids

    def _visual_evidence_rows(
        self, project_id: str, requirement_id: str = "", limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Return requirement-bound visual evidence as auxiliary context only."""
        rows = []
        for item in self.manager.list_visual_evidence_by_project(project_id):
            evidence = item.get("evidence") or {}
            bound_requirement_ids = self._evidence_requirement_ids(evidence)
            if requirement_id and requirement_id not in bound_requirement_ids:
                continue
            need_confirm = bool(
                evidence.get("need_human_confirm", item.get("need_human_confirm", True))
            )
            rows.append(
                {
                    "evidence_id": item.get("evidence_id", ""),
                    "asset_id": item.get("asset_id", ""),
                    "requirement_id": evidence.get("requirement_id", ""),
                    "related_requirement_ids": sorted(bound_requirement_ids),
                    "image_type": evidence.get("image_type")
                    or item.get("evidence_type", ""),
                    "visible_text": evidence.get("visible_text")
                    or item.get("visible_text_items")
                    or [],
                    "possible_functions": evidence.get("possible_functions") or [],
                    "possible_test_points": evidence.get("possible_test_points") or [],
                    "risk_points": evidence.get("risk_points") or [],
                    "need_human_confirm": need_confirm,
                    "usage_note": (
                        "需人工确认，不可作为唯一依据"
                        if need_confirm
                        else "仅作为辅助证据，不能覆盖文本需求"
                    ),
                }
            )
            if len(rows) >= limit:
                break
        return rows

    def _traceability_context(self, project_id: str, requirement_id: str) -> Dict[str, Any]:
        """Load only reviewed, project-scoped facts for the selected function."""
        with self.manager.connections.connection() as conn:
            atoms = [dict(row) for row in conn.execute(
                "SELECT indicator_id,indicator_text,indicator_type,source_json,rules_json,verification_scope,need_human_confirm "
                "FROM requirement_indicators WHERE project_id=? AND function_id=? ORDER BY indicator_id",
                (project_id, requirement_id),
            )]
            page_links = [dict(row) for row in conn.execute(
                "SELECT page_id,confidence,reason,status,need_human_confirm,evidence_json FROM requirement_page_links "
                "WHERE project_id=? AND function_id=? ORDER BY confidence DESC",
                (project_id, requirement_id),
            )]
            element_links = [dict(row) for row in conn.execute(
                "SELECT indicator_id,page_id,confirmed_element_id,confidence,reason,status,need_human_confirm,candidates_json "
                "FROM requirement_element_links WHERE project_id=? AND indicator_id IN "
                "(SELECT indicator_id FROM requirement_indicators WHERE project_id=? AND function_id=?)",
                (project_id, project_id, requirement_id),
            )]
            observations = [dict(row) for row in conn.execute(
                "SELECT observation_id,page_id,action,result_json,evidence_json FROM html_observations "
                "WHERE project_id=? ORDER BY observed_at DESC LIMIT 30", (project_id,)
            )]
        for row in atoms:
            row["source"] = loads_json(row.pop("source_json", "{}"), {})
            row["rules"] = loads_json(row.pop("rules_json", "{}"), {})
        for row in page_links:
            row["evidence"] = loads_json(row.pop("evidence_json", "{}"), {})
        for row in element_links:
            row["candidates"] = loads_json(row.pop("candidates_json", "[]"), [])
        for row in observations:
            row["observed_result"] = loads_json(row.pop("result_json", "{}"), {})
            row["evidence"] = loads_json(row.pop("evidence_json", "{}"), {})
        return {
            "atomic_requirements": atoms,
            "requirement_page_links": page_links,
            "requirement_element_links": element_links,
            "playwright_observations": observations,
            "evidence_policy": "html_observed is auxiliary evidence and must never replace requirement expectations",
        }

    def build(
        self,
        project_id: str,
        requirement_id: str,
        case_type: str,
        top_k_chunks: int = 5,
        top_k_history: int = 5,
        use_project_kb: bool = True,
        use_history: bool = True,
        persist: bool = True,
    ) -> Dict[str, Any]:
        profile = self.manager.get_profile(project_id) or {}
        requirement = self.manager.get_requirement(project_id, requirement_id)
        if not requirement:
            raise ValueError(f"当前项目中不存在需求：{requirement_id}")
        text = str(requirement.get("description") or requirement.get("title") or "")
        chunks = (
            search_project_chunks(self.manager, project_id, text, top_k_chunks)
            if use_project_kb
            else []
        )
        if use_project_kb and requirement.get("source_chunk_id"):
            ids = {c.get("chunk_id") for c in chunks}
            for chunk in self.manager.list_chunks(project_id, 2000):
                if (
                    chunk.get("chunk_id") == requirement.get("source_chunk_id")
                    and chunk.get("chunk_id") not in ids
                ):
                    chunks.insert(0, chunk)
                    break
        scenarios = self.manager.list_scenario_cards(project_id, requirement_id)
        req_item = RequirementItem(
            requirement_id=requirement_id,
            requirement_text=text,
            test_object=str(profile.get("test_object") or ""),
        )
        six = classify_requirement(req_item, False, None)
        methods = match_test_methods(req_item, six)
        similar_rows = []
        if use_history and self.case_library is not None and top_k_history > 0:
            found = self.case_library.search_similar_cases(
                text,
                six[0] if len(six) == 1 else None,
                None,
                str(profile.get("domain") or ""),
                top_k_history,
            )
            similar_rows = [
                {
                    "case_id": item.case_id,
                    "case_name": item.case_name,
                    "test_method": item.test_method,
                    "test_steps": item.test_steps,
                    "expected_result": item.expected_result,
                    "score": score,
                    "reference_reason": reason,
                }
                for item, score, reason in found
            ]
        visual_rows = self._visual_evidence_rows(project_id, requirement_id)
        traceability = self._traceability_context(project_id, requirement_id)
        missing = []
        if not profile:
            missing.append("项目画像")
        if not scenarios:
            missing.append("关联场景卡片")
        if not chunks:
            missing.append("需求相关项目片段")
        if not profile.get("test_object"):
            missing.append("明确测试对象")
        if not any(s.get("input_data") for s in scenarios):
            missing.append("场景输入数据")
        if case_type == "性能测试" and not any(
            s.get("performance_constraints") for s in scenarios
        ):
            missing.append("性能判定阈值")
        context = {
            "project_id": project_id,
            "requirement_id": requirement_id,
            "case_type": case_type,
            "project_profile": profile,
            "requirement": requirement,
            "related_chunks": chunks,
            "related_scenario_cards": scenarios,
            "matched_test_methods": methods,
            "six_quality_attributes": six,
            "similar_library_cases": similar_rows,
            "visual_evidence": visual_rows,
            "traceability_context": traceability,
            "evidence_context": {
                "text_document_evidence": chunks,
                "requirement_points": [requirement],
                "scenario_cards": scenarios,
                "history_case_references": similar_rows,
                "visual_evidence": visual_rows,
                "atomic_requirements": traceability["atomic_requirements"],
                "requirement_page_links": traceability["requirement_page_links"],
                "requirement_element_links": traceability["requirement_element_links"],
                "playwright_observations": traceability["playwright_observations"],
                "visual_evidence_policy": "视觉证据仅作为辅助证据，不能覆盖文本需求；标记需人工确认的证据不可作为唯一依据。",
            },
            "missing_information": missing,
            "generation_constraints": [
                "只使用当前 project_id 的画像、需求、片段、场景卡和视觉证据",
                "文本需求与文档片段优先，视觉证据只能作为辅助证据，不能覆盖文本需求",
                "need_human_confirm=true 的视觉证据需人工确认，不可作为唯一依据",
                "历史用例只参考写法和方法，不作为项目事实",
                "资料未给出阈值时使用“按需求文档规定值判定”或“需人工确认”",
                "禁止补造接口字段、设备型号、环境参数和性能指标",
            ],
        }
        context_id = (
            self.manager.save_generation_context(
                project_id, requirement_id, case_type, context
            )
            if persist
            else ""
        )
        context["context_id"] = context_id
        return context
