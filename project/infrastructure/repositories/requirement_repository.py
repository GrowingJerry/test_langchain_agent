"""Project requirement persistence."""

from typing import Any, Dict, List, Optional

from infrastructure.database.json_codec import dumps_json, loads_json
from infrastructure.repositories.base import BaseRepository, now_iso


class RequirementRepository(BaseRepository):
    def replace(self, project_id: str, rows: List[Dict[str, Any]]) -> None:
        with self.connections.transaction() as conn:
            conn.execute(
                "DELETE FROM project_requirements WHERE project_id=?", (project_id,)
            )
            for row in rows:
                self._upsert(conn, project_id, row)

    def upsert(self, project_id: str, row: Dict[str, Any]) -> None:
        with self.connections.transaction() as conn:
            self._upsert(conn, project_id, row)

    @staticmethod
    def _upsert(conn: Any, project_id: str, row: Dict[str, Any]) -> None:
        source_chunk_ids = list(row.get("source_chunk_ids") or [])
        if row.get("source_chunk_id") and row.get("source_chunk_id") not in source_chunk_ids:
            source_chunk_ids.append(str(row.get("source_chunk_id")))
        source_documents = list(row.get("source_documents") or [])
        if row.get("source_document") and row.get("source_document") not in source_documents:
            source_documents.append(str(row.get("source_document")))
        conn.execute(
            """INSERT INTO project_requirements(
            requirement_id,project_id,title,description,category,source_document,source_chunk_id,created_at,
            requirement_type,section_number,section_path_json,test_object,actors_json,preconditions_json,
            inputs_json,processing_rules_json,outputs_json,exception_rules_json,performance_constraints_json,
            interface_constraints_json,security_constraints_json,acceptance_criteria_json,priority,
            verification_method,parent_requirement_ids_json,recommended_test_type,alternative_test_types_json,
            test_type_confidence,test_type_reasons_json,source_chunk_ids_json,source_documents_json,
            source_evidence_json,need_human_confirm,missing_information_json
            ,machine_extraction_json,review_changes_json,retained
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id,requirement_id) DO UPDATE SET
            title=excluded.title,description=excluded.description,category=excluded.category,
            source_document=excluded.source_document,source_chunk_id=excluded.source_chunk_id,
            requirement_type=excluded.requirement_type,section_number=excluded.section_number,
            section_path_json=excluded.section_path_json,test_object=excluded.test_object,
            actors_json=excluded.actors_json,preconditions_json=excluded.preconditions_json,
            inputs_json=excluded.inputs_json,processing_rules_json=excluded.processing_rules_json,
            outputs_json=excluded.outputs_json,exception_rules_json=excluded.exception_rules_json,
            performance_constraints_json=excluded.performance_constraints_json,
            interface_constraints_json=excluded.interface_constraints_json,
            security_constraints_json=excluded.security_constraints_json,
            acceptance_criteria_json=excluded.acceptance_criteria_json,priority=excluded.priority,
            verification_method=excluded.verification_method,parent_requirement_ids_json=excluded.parent_requirement_ids_json,
            recommended_test_type=excluded.recommended_test_type,
            alternative_test_types_json=excluded.alternative_test_types_json,
            test_type_confidence=excluded.test_type_confidence,
            test_type_reasons_json=excluded.test_type_reasons_json,
            source_chunk_ids_json=excluded.source_chunk_ids_json,
            source_documents_json=excluded.source_documents_json,
            source_evidence_json=excluded.source_evidence_json,
            need_human_confirm=excluded.need_human_confirm,
            missing_information_json=excluded.missing_information_json,
            machine_extraction_json=excluded.machine_extraction_json,
            review_changes_json=excluded.review_changes_json,
            retained=excluded.retained""",
            (
                row.get("requirement_id", ""),
                project_id,
                row.get("title", ""),
                row.get("description", ""),
                row.get("category", ""),
                row.get("source_document", "manual_input"),
                row.get("source_chunk_id", ""),
                now_iso(),
                row.get("requirement_type", ""),
                row.get("section_number", ""),
                dumps_json(row.get("section_path") or []),
                row.get("test_object", ""),
                dumps_json(row.get("actors") or []),
                dumps_json(row.get("preconditions") or []),
                dumps_json(row.get("inputs") or []),
                dumps_json(row.get("processing_rules") or []),
                dumps_json(row.get("outputs") or []),
                dumps_json(row.get("exception_rules") or []),
                dumps_json(row.get("performance_constraints") or []),
                dumps_json(row.get("interface_constraints") or []),
                dumps_json(row.get("security_constraints") or []),
                dumps_json(row.get("acceptance_criteria") or []),
                row.get("priority", ""),
                row.get("verification_method", ""),
                dumps_json(row.get("parent_requirement_ids") or []),
                row.get("recommended_test_type", ""),
                dumps_json(row.get("alternative_test_types") or []),
                float(row.get("test_type_confidence") or 0),
                dumps_json(row.get("test_type_reasons") or []),
                dumps_json(source_chunk_ids),
                dumps_json(source_documents),
                dumps_json(row.get("source_evidence") or []),
                int(bool(row.get("need_human_confirm"))),
                dumps_json(row.get("missing_information") or []),
                dumps_json(row.get("machine_extraction") or {}),
                dumps_json(row.get("review_changes") or {}),
                int(bool(row.get("retained", True))),
            ),
        )

    @staticmethod
    def _decode(row: Any) -> Dict[str, Any]:
        item = dict(row)
        mappings = {
            "section_path_json": ("section_path", []),
            "actors_json": ("actors", []),
            "preconditions_json": ("preconditions", []),
            "inputs_json": ("inputs", []),
            "processing_rules_json": ("processing_rules", []),
            "outputs_json": ("outputs", []),
            "exception_rules_json": ("exception_rules", []),
            "performance_constraints_json": ("performance_constraints", []),
            "interface_constraints_json": ("interface_constraints", []),
            "security_constraints_json": ("security_constraints", []),
            "acceptance_criteria_json": ("acceptance_criteria", []),
            "parent_requirement_ids_json": ("parent_requirement_ids", []),
            "alternative_test_types_json": ("alternative_test_types", []),
            "test_type_reasons_json": ("test_type_reasons", []),
            "source_chunk_ids_json": ("source_chunk_ids", []),
            "source_documents_json": ("source_documents", []),
            "source_evidence_json": ("source_evidence", []),
            "missing_information_json": ("missing_information", []),
            "machine_extraction_json": ("machine_extraction", {}),
            "review_changes_json": ("review_changes", {}),
        }
        for source, (target, default) in mappings.items():
            item[target] = loads_json(item.get(source), default)
        if not item["source_chunk_ids"] and item.get("source_chunk_id"):
            item["source_chunk_ids"] = [item["source_chunk_id"]]
        if not item["source_documents"] and item.get("source_document"):
            item["source_documents"] = [item["source_document"]]
        item["need_human_confirm"] = bool(item.get("need_human_confirm"))
        item["retained"] = bool(item.get("retained", True))
        return item

    def list(self, project_id: str) -> List[Dict[str, Any]]:
        with self.connections.connection() as conn:
            return [
                self._decode(row)
                for row in conn.execute(
                    "SELECT * FROM project_requirements WHERE project_id=? ORDER BY requirement_id",
                    (project_id,),
                )
            ]

    def get(self, project_id: str, requirement_id: str) -> Optional[Dict[str, Any]]:
        with self.connections.connection() as conn:
            row = conn.execute(
                "SELECT * FROM project_requirements WHERE project_id=? AND requirement_id=?",
                (project_id, requirement_id),
            ).fetchone()
            return self._decode(row) if row else None
