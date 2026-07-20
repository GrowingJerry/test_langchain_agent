"""Project-scoped fact collection for deterministic scenario compilation."""

from __future__ import annotations

from typing import Any, Dict, List

from domain.schemas.scenario_spec import ScenarioIntent
from infrastructure.retrieval.hybrid_search import keyword_score, tokenize_query
from learning.knowledge_review_service import KnowledgeReviewService
from learning.feedback_learning_service import FeedbackLearningService


class ScenarioContextBuilder:
    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def build(self, intent: ScenarioIntent, top_k_chunks: int = 20) -> Dict[str, Any]:
        project_id = intent.project_id
        profile = self.manager.get_profile(project_id) or {}
        query = " ".join(
            [intent.effective_goal, intent.simulation_object, intent.target_subsystem,
             intent.mission_phase, *intent.focus_risks]
        )
        tokens = tokenize_query(query)
        requirements = self._rank(
            self.manager.list_requirements(project_id), tokens, "description", 30
        )
        chunks = self._rank(
            self.manager.list_chunks(project_id, limit=5000), tokens, "content", top_k_chunks
        )
        knowledge = [
            row for row in KnowledgeReviewService(self.manager).query_for_scenario(project_id)
            if row.get("status") == "approved"
            and self._knowledge_relevant(row, tokens, intent.target_subsystem)
        ]
        feedback_context = {
            "scenario_goal": intent.effective_goal,
            "simulation_object": intent.simulation_object,
            "target_subsystem": intent.target_subsystem,
            "mission_phase": intent.mission_phase,
            "focus_risks": intent.focus_risks,
        }
        feedback_rules = FeedbackLearningService(self.manager).match_approved_rules(
            project_id, feedback_context, allow_global=intent.use_project_defaults
        )
        return {
            "project": self.manager.get_project(project_id) or {},
            "profile": profile,
            "requirements": requirements,
            "knowledge": knowledge,
            "chunks": chunks,
            "source_chunk_ids": [str(row["chunk_id"]) for row in chunks],
            "knowledge_unit_ids": [str(row["knowledge_unit_id"]) for row in knowledge],
            "feedback_rules": feedback_rules,
            "feedback_rule_ids": [row["learning_rule_id"] for row in feedback_rules],
        }

    @staticmethod
    def _rank(rows: List[Dict[str, Any]], tokens: List[str], field: str, limit: int) -> List[Dict[str, Any]]:
        scored = []
        for row in rows:
            text = str(row.get(field) or row.get("title") or "")
            score = keyword_score(text, tokens)
            if score > 0:
                scored.append((score, str(row.get("chunk_id") or row.get("requirement_id") or ""), row))
        return [row for _, _, row in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]]

    @staticmethod
    def _knowledge_relevant(row: Dict[str, Any], tokens: List[str], subsystem: str) -> bool:
        text = " ".join([str(row.get("title") or ""), str(row.get("content") or ""),
                         str(row.get("section_scope") or "")]).lower()
        return bool((subsystem and subsystem.lower() in text) or keyword_score(text, tokens) > 0)
