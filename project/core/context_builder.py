"""Build and persist grounded context for scenario-adapted case generation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.project_kb import search_project_chunks
from core.six_quality_classifier import classify_requirement
from core.test_method_matcher import match_test_methods
from models.schemas import RequirementItem


class ContextBuilder:
    def __init__(self, manager, case_library: Optional[Any] = None):
        self.manager = manager
        self.case_library = case_library

    def _visual_evidence_rows(
        self, project_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Return project-scoped visual evidence as auxiliary context only."""
        rows = []
        for item in self.manager.list_visual_evidence_by_project(project_id)[:limit]:
            evidence = item.get("evidence") or {}
            need_confirm = bool(
                evidence.get("need_human_confirm", item.get("need_human_confirm", True))
            )
            rows.append(
                {
                    "evidence_id": item.get("evidence_id", ""),
                    "asset_id": item.get("asset_id", ""),
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
        return rows

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
        visual_rows = self._visual_evidence_rows(project_id)
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
            "evidence_context": {
                "text_document_evidence": chunks,
                "requirement_points": [requirement],
                "scenario_cards": scenarios,
                "history_case_references": similar_rows,
                "visual_evidence": visual_rows,
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
