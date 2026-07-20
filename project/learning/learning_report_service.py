"""Deterministic coverage reports for project domain learning tasks."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from domain.schemas.learning import KnowledgeCoverageCandidate, LearningTask
from infrastructure.db.json_codec import loads_json


SCENARIO_KNOWLEDGE = {
    "state_transition": "状态变化场景",
    "fault_mode": "故障与异常场景",
    "environment_factor": "环境扰动场景",
    "constraint": "边界与约束场景",
    "verification_rule": "验证场景",
    "scenario_pattern": "场景模板",
    "input_output": "接口交互场景",
}


class LearningReportService:
    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def build(
        self,
        task: LearningTask,
        candidates: List[KnowledgeCoverageCandidate],
        units: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        candidate_documents = Counter(item.document_id for item in candidates)
        documents = {
            row["document_id"]: row
            for row in self.manager.list_documents(task.project_id)
            if row["document_id"] in task.document_ids
        }
        document_coverage = [
            {
                "document_id": document_id,
                "filename": documents.get(document_id, {}).get("filename", ""),
                "candidate_count": candidate_documents.get(document_id, 0),
                "covered": candidate_documents.get(document_id, 0) > 0,
            }
            for document_id in task.document_ids
        ]
        type_counts = Counter(str(row.get("knowledge_type") or "") for row in units)
        scenario_counts = Counter(
            SCENARIO_KNOWLEDGE[kind]
            for kind in type_counts
            if kind in SCENARIO_KNOWLEDGE
            for _ in range(type_counts[kind])
        )
        matched = {topic for item in candidates for topic in item.matched_topics}
        missing = [topic for topic in task.target_topics if topic.lower() not in matched]
        warnings: List[str] = []
        for document in documents.values():
            report = loads_json(document.get("parse_report_json"), {})
            warnings.extend(str(item) for item in report.get("warnings", []))
        return {
            "task_id": task.task_id,
            "document_coverage": document_coverage,
            "knowledge_type_coverage": dict(type_counts),
            "scenario_capability_coverage": dict(scenario_counts),
            "missing_topics": missing,
            "parse_warnings": list(dict.fromkeys(warnings))[:100],
            "pending_review_count": sum(
                1 for row in units if row.get("status") == "draft"
            ),
        }
