"""Application service orchestrating the two-stage project learning flow."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from chains.knowledge_extraction import KnowledgeExtractionChain
from domain.schemas.learning import LearningTask
from infrastructure.db.json_codec import dumps_json, loads_json
from infrastructure.db.repositories.base import new_id, now_iso
from learning.knowledge_extractor import KnowledgeExtractor
from learning.learning_report_service import LearningReportService


class LearningTaskService:
    def __init__(
        self, manager: Any, chain: Optional[KnowledgeExtractionChain] = None
    ) -> None:
        self.manager = manager
        self.extractor = KnowledgeExtractor(
            manager, chain or KnowledgeExtractionChain()
        )
        self.reports = LearningReportService(manager)

    def create_task(
        self,
        project_id: str,
        *,
        task_name: str,
        learning_goal: str,
        domain: str,
        simulation_object: str,
        target_subsystems: List[str],
        target_topics: List[str],
        expected_scenario_types: List[str],
        excluded_topics: List[str],
        selected_document_ids: List[str],
    ) -> LearningTask:
        if not task_name.strip() or not learning_goal.strip():
            raise ValueError("task_name 和 learning_goal 不能为空")
        owned = {
            row["document_id"] for row in self.manager.list_documents(project_id)
        }
        selected = list(dict.fromkeys(selected_document_ids))
        invalid = [item for item in selected if item not in owned]
        if not selected or invalid:
            raise ValueError(f"选定文档为空或不属于当前项目：{invalid}")
        timestamp = now_iso()
        task = LearningTask(
            task_id=new_id("LRN"), project_id=project_id,
            task_type="domain_learning", task_name=task_name.strip(),
            learning_goal=learning_goal.strip(), domain=domain.strip(),
            simulation_object=simulation_object.strip(),
            target_subsystems=target_subsystems, target_topics=target_topics,
            expected_scenario_types=expected_scenario_types,
            excluded_topics=excluded_topics, document_ids=selected,
            progress_total=len(selected), created_at=timestamp, updated_at=timestamp,
        )
        with self.manager.connections.transaction() as conn:
            conn.execute(
                """INSERT INTO learning_tasks(
                task_id,project_id,task_type,status,document_ids_json,
                processed_document_ids_json,failed_document_ids_json,progress_current,
                progress_total,error_messages_json,created_at,updated_at,task_name,
                learning_goal,domain,simulation_object,target_subsystems_json,
                target_topics_json,expected_scenario_types_json,excluded_topics_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task.task_id, project_id, task.task_type, task.status,
                    dumps_json(selected), "[]", "[]", 0, len(selected), "[]",
                    timestamp, timestamp, task.task_name, task.learning_goal,
                    task.domain, task.simulation_object,
                    dumps_json(target_subsystems), dumps_json(target_topics),
                    dumps_json(expected_scenario_types), dumps_json(excluded_topics),
                ),
            )
        return task

    def run_task(self, project_id: str, task_id: str) -> Dict[str, Any]:
        task = self.get_task(project_id, task_id)
        if task is None:
            raise ValueError("学习任务不存在或不属于当前项目")
        existing = self.extractor.list_task_units(project_id, task_id)
        with self.manager.connections.transaction() as conn:
            conn.execute(
                "UPDATE learning_tasks SET status='retrieving',updated_at=? WHERE project_id=? AND task_id=?",
                (now_iso(), project_id, task_id),
            )
        candidates = self.extractor.retrieve_candidates(task)
        with self.manager.connections.transaction() as conn:
            conn.execute(
                """UPDATE learning_tasks SET coverage_candidates_json=?,status='extracting',
                updated_at=? WHERE project_id=? AND task_id=?""",
                (dumps_json([item.model_dump(mode="json") for item in candidates]),
                 now_iso(), project_id, task_id),
            )
        try:
            units = existing or self.extractor.extract_and_persist(task, candidates)
            report = self.reports.build(task, candidates, units)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            with self.manager.connections.transaction() as conn:
                conn.execute(
                    """UPDATE learning_tasks SET status='failed',error_messages_json=?,
                    updated_at=? WHERE project_id=? AND task_id=?""",
                    (dumps_json([message]), now_iso(), project_id, task_id),
                )
            return {"task_id": task_id, "status": "failed", "errors": [message],
                    "coverage_candidates": [item.model_dump(mode="json") for item in candidates]}
        with self.manager.connections.transaction() as conn:
            conn.execute(
                """UPDATE learning_tasks SET status='completed',progress_current=?,
                processed_document_ids_json=?,report_json=?,updated_at=?
                WHERE project_id=? AND task_id=?""",
                (len(task.document_ids), dumps_json(task.document_ids), dumps_json(report),
                 now_iso(), project_id, task_id),
            )
        return {"task_id": task_id, "status": "completed", "knowledge_units": units,
                "coverage_candidates": [item.model_dump(mode="json") for item in candidates],
                "report": report}

    def get_task(self, project_id: str, task_id: str) -> Optional[LearningTask]:
        with self.manager.connections.connection() as conn:
            raw = conn.execute(
                "SELECT * FROM learning_tasks WHERE project_id=? AND task_id=?",
                (project_id, task_id),
            ).fetchone()
        if not raw:
            return None
        row = dict(raw)
        return LearningTask(
            task_id=row["task_id"], project_id=row["project_id"],
            task_type=row["task_type"], status=row["status"],
            task_name=row.get("task_name") or "", learning_goal=row.get("learning_goal") or "",
            domain=row.get("domain") or "", simulation_object=row.get("simulation_object") or "",
            target_subsystems=loads_json(row.get("target_subsystems_json"), []),
            target_topics=loads_json(row.get("target_topics_json"), []),
            expected_scenario_types=loads_json(row.get("expected_scenario_types_json"), []),
            excluded_topics=loads_json(row.get("excluded_topics_json"), []),
            document_ids=loads_json(row.get("document_ids_json"), []),
            processed_document_ids=loads_json(row.get("processed_document_ids_json"), []),
            failed_document_ids=loads_json(row.get("failed_document_ids_json"), []),
            progress_current=int(row.get("progress_current") or 0),
            progress_total=int(row.get("progress_total") or 0),
            error_messages=loads_json(row.get("error_messages_json"), []),
            created_at=row.get("created_at") or "", updated_at=row.get("updated_at") or "",
        )

    def list_tasks(self, project_id: str) -> List[Dict[str, Any]]:
        with self.manager.connections.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM learning_tasks WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            )
            result = []
            for raw in rows:
                row = dict(raw)
                for source, target, default in (
                    ("document_ids_json", "document_ids", []),
                    ("target_topics_json", "target_topics", []),
                    ("error_messages_json", "error_messages", []),
                    ("report_json", "report", {}),
                ):
                    row[target] = loads_json(row.get(source), default)
                result.append(row)
            return result
