"""Additive, idempotent migrations for the project workspace database."""

from __future__ import annotations

import re
import sqlite3
from typing import Dict

from infrastructure.database.connection import SQLiteConnectionManager

SCHEMA_VERSION = 22
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ensure_columns(
    cursor: sqlite3.Cursor, table: str, columns: Dict[str, str]
) -> None:
    if not _IDENTIFIER.fullmatch(table):
        raise ValueError(f"Invalid table identifier: {table}")
    existing = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():
        if not _IDENTIFIER.fullmatch(name):
            raise ValueError(f"Invalid column identifier: {name}")
        if name not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def migrate_database(connections: SQLiteConnectionManager) -> None:
    """Create or upgrade a database without deleting tables, columns, or rows."""
    with connections.transaction() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_versions (
                version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS projects (
                project_id TEXT PRIMARY KEY, project_name TEXT NOT NULL, description TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS project_documents (
                document_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, filename TEXT NOT NULL,
                file_type TEXT, file_path TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS project_chunks (
                chunk_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(document_id) REFERENCES project_documents(document_id)
            );
            CREATE TABLE IF NOT EXISTS project_profiles (
                project_id TEXT PRIMARY KEY, project_name TEXT, domain TEXT, test_object TEXT,
                main_functions TEXT, interfaces TEXT, quality_attributes TEXT, constraints TEXT,
                raw_json TEXT, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS project_requirements (
                requirement_id TEXT NOT NULL, project_id TEXT NOT NULL, title TEXT, description TEXT,
                category TEXT, source_document TEXT, source_chunk_id TEXT, created_at TEXT NOT NULL,
                PRIMARY KEY(project_id, requirement_id),
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS generation_runs (
                run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, run_type TEXT, status TEXT,
                model_name TEXT, prompt_snapshot TEXT, created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS generated_cases (
                case_id TEXT NOT NULL, project_id TEXT NOT NULL, requirement_id TEXT, case_type TEXT,
                source_chunk_ids TEXT, generation_run_id TEXT, case_json TEXT, created_at TEXT NOT NULL,
                PRIMARY KEY(project_id, case_id), FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS trace_sources (
                trace_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, artifact_type TEXT,
                artifact_id TEXT, source_document TEXT, source_chunk_id TEXT, created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS project_chunk_embeddings (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL, embedding_json TEXT NOT NULL, model_name TEXT NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(project_id, chunk_id, model_name),
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(chunk_id) REFERENCES project_chunks(chunk_id)
            );
            CREATE TABLE IF NOT EXISTS project_assets (
                asset_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document_id TEXT, asset_type TEXT,
                file_path TEXT, page_no INTEGER, source_document TEXT, created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(document_id) REFERENCES project_documents(document_id)
            );
            CREATE TABLE IF NOT EXISTS project_visual_evidence (
                evidence_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                evidence_type TEXT, evidence_json TEXT, visible_text TEXT, source_region TEXT,
                confidence REAL, need_human_confirm INTEGER, raw_response TEXT, created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(asset_id) REFERENCES project_assets(asset_id)
            );
            CREATE TABLE IF NOT EXISTS review_results (
                review_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, case_id TEXT NOT NULL,
                review_type TEXT, status TEXT, issues_json TEXT, reviewed_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS scenario_cards (
                scenario_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, requirement_id TEXT,
                title TEXT, preconditions TEXT, actions TEXT, expected_outcome TEXT,
                source_chunk_id TEXT, created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS case_generation_contexts (
                context_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, requirement_id TEXT NOT NULL,
                case_type TEXT, context_json TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS case_quality_scores (
                score_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, case_id TEXT NOT NULL,
                context_id TEXT, score REAL NOT NULL, dimensions_json TEXT, issues_json TEXT,
                suggestions_json TEXT, created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS learning_tasks (
                task_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_type TEXT NOT NULL,
                status TEXT NOT NULL, document_ids_json TEXT NOT NULL DEFAULT '[]',
                processed_document_ids_json TEXT NOT NULL DEFAULT '[]',
                failed_document_ids_json TEXT NOT NULL DEFAULT '[]', progress_current INTEGER NOT NULL DEFAULT 0,
                progress_total INTEGER NOT NULL DEFAULT 0, error_messages_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS document_processing_jobs (
                job_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document_id TEXT NOT NULL,
                learning_task_id TEXT, status TEXT NOT NULL, stage TEXT, content_hash TEXT,
                parser_name TEXT, statistics_json TEXT NOT NULL DEFAULT '{}', error_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(document_id) REFERENCES project_documents(document_id),
                FOREIGN KEY(learning_task_id) REFERENCES learning_tasks(task_id)
            );
            CREATE TABLE IF NOT EXISTS document_sections (
                section_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, document_id TEXT NOT NULL,
                title TEXT, section_path_json TEXT NOT NULL DEFAULT '[]', content TEXT NOT NULL,
                page_start INTEGER, page_end INTEGER, chunk_ids_json TEXT NOT NULL DEFAULT '[]',
                sequence_no INTEGER NOT NULL DEFAULT 0, chunk_id TEXT, page_no INTEGER, jsonl_record_no INTEGER,
                created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(document_id) REFERENCES project_documents(document_id)
            );
            CREATE TABLE IF NOT EXISTS knowledge_units (
                knowledge_unit_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, knowledge_type TEXT NOT NULL,
                title TEXT, content TEXT NOT NULL, normalized_data_json TEXT NOT NULL DEFAULT '{}',
                tags_json TEXT NOT NULL DEFAULT '[]', document_id TEXT, chunk_id TEXT, page_no INTEGER,
                jsonl_record_no INTEGER, confidence REAL NOT NULL DEFAULT 0,
                need_human_confirm INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS knowledge_relations (
                relation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_knowledge_unit_id TEXT NOT NULL,
                target_knowledge_unit_id TEXT NOT NULL, relation_type TEXT NOT NULL,
                attributes_json TEXT NOT NULL DEFAULT '{}', document_id TEXT, chunk_id TEXT,
                page_no INTEGER, jsonl_record_no INTEGER, created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(source_knowledge_unit_id) REFERENCES knowledge_units(knowledge_unit_id),
                FOREIGN KEY(target_knowledge_unit_id) REFERENCES knowledge_units(knowledge_unit_id)
            );
            CREATE TABLE IF NOT EXISTS knowledge_conflicts (
                conflict_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                knowledge_unit_ids_json TEXT NOT NULL DEFAULT '[]', conflict_type TEXT NOT NULL,
                description TEXT, status TEXT NOT NULL DEFAULT 'open', resolution TEXT,
                document_id TEXT, chunk_id TEXT, page_no INTEGER, jsonl_record_no INTEGER,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS knowledge_reviews (
                review_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, knowledge_unit_id TEXT NOT NULL,
                status TEXT NOT NULL, reviewer TEXT, comments_json TEXT NOT NULL DEFAULT '[]',
                corrections_json TEXT NOT NULL DEFAULT '{}', document_id TEXT, chunk_id TEXT,
                page_no INTEGER, jsonl_record_no INTEGER, reviewed_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(knowledge_unit_id) REFERENCES knowledge_units(knowledge_unit_id)
            );
            CREATE TABLE IF NOT EXISTS equipment_entities (
                equipment_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL,
                category TEXT, equipment_type TEXT, country TEXT, attributes_json TEXT NOT NULL DEFAULT '{}',
                document_id TEXT, chunk_id TEXT, page_no INTEGER, jsonl_record_no INTEGER,
                need_human_confirm INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS equipment_aliases (
                alias_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, equipment_id TEXT NOT NULL,
                alias TEXT NOT NULL, document_id TEXT, chunk_id TEXT, page_no INTEGER,
                jsonl_record_no INTEGER, created_at TEXT NOT NULL,
                UNIQUE(project_id, equipment_id, alias),
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(equipment_id) REFERENCES equipment_entities(equipment_id)
            );
            CREATE TABLE IF NOT EXISTS equipment_capabilities (
                capability_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, equipment_id TEXT NOT NULL,
                name TEXT NOT NULL, description TEXT, capability_type TEXT,
                parameters_json TEXT NOT NULL DEFAULT '{}', constraints_json TEXT NOT NULL DEFAULT '[]',
                document_id TEXT, chunk_id TEXT, page_no INTEGER, jsonl_record_no INTEGER,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(equipment_id) REFERENCES equipment_entities(equipment_id)
            );
            CREATE TABLE IF NOT EXISTS equipment_role_mappings (
                mapping_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, equipment_id TEXT NOT NULL,
                role_name TEXT NOT NULL, capability_ids_json TEXT NOT NULL DEFAULT '[]',
                constraints_json TEXT NOT NULL DEFAULT '[]', priority INTEGER NOT NULL DEFAULT 0,
                document_id TEXT, chunk_id TEXT, page_no INTEGER, jsonl_record_no INTEGER,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(equipment_id) REFERENCES equipment_entities(equipment_id)
            );
            CREATE TABLE IF NOT EXISTS equipment_configuration_rules (
                rule_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, description TEXT,
                condition_expression TEXT, required_roles_json TEXT NOT NULL DEFAULT '[]',
                required_capabilities_json TEXT NOT NULL DEFAULT '[]',
                compatible_equipment_types_json TEXT NOT NULL DEFAULT '[]',
                incompatible_equipment_ids_json TEXT NOT NULL DEFAULT '[]',
                parameters_json TEXT NOT NULL DEFAULT '{}', enabled INTEGER NOT NULL DEFAULT 1,
                document_id TEXT, chunk_id TEXT, page_no INTEGER, jsonl_record_no INTEGER,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS project_equipment_inventory (
                inventory_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, equipment_id TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'available',
                configuration_json TEXT NOT NULL DEFAULT '{}', notes_json TEXT NOT NULL DEFAULT '[]',
                document_id TEXT, chunk_id TEXT, page_no INTEGER, jsonl_record_no INTEGER,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(project_id, equipment_id, inventory_id),
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(equipment_id) REFERENCES equipment_entities(equipment_id)
            );
            CREATE TABLE IF NOT EXISTS scenario_templates (
                template_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL,
                scenario_category TEXT, template_json TEXT NOT NULL DEFAULT '{}', version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'active', document_id TEXT, chunk_id TEXT, page_no INTEGER,
                jsonl_record_no INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS scenario_generation_runs (
                run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scenario_id TEXT,
                template_id TEXT, status TEXT NOT NULL, generation_mode TEXT, model_name TEXT,
                input_json TEXT NOT NULL DEFAULT '{}', output_json TEXT NOT NULL DEFAULT '{}',
                error_json TEXT NOT NULL DEFAULT '{}', document_id TEXT, chunk_id TEXT, page_no INTEGER,
                jsonl_record_no INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(template_id) REFERENCES scenario_templates(template_id)
            );
            CREATE TABLE IF NOT EXISTS scenario_equipment_allocations (
                allocation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scenario_id TEXT NOT NULL,
                role_requirement_id TEXT NOT NULL, equipment_id TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1,
                configuration_json TEXT NOT NULL DEFAULT '{}', capability_ids_json TEXT NOT NULL DEFAULT '[]',
                rule_ids_json TEXT NOT NULL DEFAULT '[]', assumptions_json TEXT NOT NULL DEFAULT '[]',
                need_human_confirm INTEGER NOT NULL DEFAULT 0, document_id TEXT, chunk_id TEXT,
                page_no INTEGER, jsonl_record_no INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(equipment_id) REFERENCES equipment_entities(equipment_id)
            );
            CREATE TABLE IF NOT EXISTS scenario_validation_results (
                validation_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, scenario_id TEXT NOT NULL,
                is_valid INTEGER NOT NULL, errors_json TEXT NOT NULL DEFAULT '[]',
                warnings_json TEXT NOT NULL DEFAULT '[]', missing_roles_json TEXT NOT NULL DEFAULT '[]',
                conflicting_rule_ids_json TEXT NOT NULL DEFAULT '[]',
                checked_allocation_ids_json TEXT NOT NULL DEFAULT '[]', metrics_json TEXT NOT NULL DEFAULT '{}',
                need_human_confirm INTEGER NOT NULL DEFAULT 0, document_id TEXT, chunk_id TEXT,
                page_no INTEGER, jsonl_record_no INTEGER, created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS feedback_candidates (
                candidate_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, candidate_type TEXT NOT NULL,
                target_artifact_type TEXT, target_artifact_id TEXT, feedback_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending', document_id TEXT, chunk_id TEXT, page_no INTEGER,
                jsonl_record_no INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS approved_learning_rules (
                learning_rule_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL,
                rule_type TEXT NOT NULL, condition_json TEXT NOT NULL DEFAULT '{}', action_json TEXT NOT NULL DEFAULT '{}',
                source_feedback_candidate_id TEXT, enabled INTEGER NOT NULL DEFAULT 1,
                document_id TEXT, chunk_id TEXT, page_no INTEGER, jsonl_record_no INTEGER,
                approved_by TEXT, approved_at TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(source_feedback_candidate_id) REFERENCES feedback_candidates(candidate_id)
            );
            CREATE TABLE IF NOT EXISTS feedback_corrections (
                correction_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                original_value_json TEXT NOT NULL DEFAULT 'null',
                corrected_value_json TEXT NOT NULL DEFAULT 'null', field_name TEXT NOT NULL,
                entity_type TEXT NOT NULL, correction_reason TEXT NOT NULL DEFAULT '',
                scenario_id TEXT, requirement_ids_json TEXT NOT NULL DEFAULT '[]',
                source_context_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'candidate', candidate_id TEXT,
                created_at TEXT NOT NULL, document_id TEXT, chunk_id TEXT, page_no INTEGER,
                jsonl_record_no INTEGER, FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(candidate_id) REFERENCES feedback_candidates(candidate_id)
            );
            CREATE TABLE IF NOT EXISTS feedback_rule_audit (
                audit_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, learning_rule_id TEXT NOT NULL,
                action TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
                document_id TEXT, chunk_id TEXT, page_no INTEGER, jsonl_record_no INTEGER,
                FOREIGN KEY(project_id) REFERENCES projects(project_id),
                FOREIGN KEY(learning_rule_id) REFERENCES approved_learning_rules(learning_rule_id)
            );
            CREATE TABLE IF NOT EXISTS requirement_nodes (
                project_id TEXT NOT NULL, node_id TEXT NOT NULL, parent_id TEXT, identifier TEXT NOT NULL,
                name TEXT NOT NULL, level INTEGER NOT NULL, hierarchy_path_json TEXT NOT NULL DEFAULT '[]',
                sections_json TEXT NOT NULL DEFAULT '{}', source_document TEXT, source_block_id TEXT,
                identifier_generated INTEGER NOT NULL DEFAULT 0, need_human_confirm INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(project_id, node_id),
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS requirement_indicators (
                project_id TEXT NOT NULL, indicator_id TEXT NOT NULL, capability_id TEXT, function_id TEXT NOT NULL,
                parent_indicator_id TEXT, indicator_text TEXT NOT NULL, indicator_type TEXT NOT NULL,
                source_json TEXT NOT NULL DEFAULT '{}', rules_json TEXT NOT NULL DEFAULT '{}',
                verification_scope TEXT NOT NULL DEFAULT 'offline', need_human_confirm INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(project_id, indicator_id),
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS html_pages (
                project_id TEXT NOT NULL, page_id TEXT NOT NULL, title TEXT, page_path TEXT NOT NULL,
                source_asset TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(project_id, page_id), FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS html_elements (
                project_id TEXT NOT NULL, element_id TEXT NOT NULL, page_id TEXT NOT NULL, tag TEXT NOT NULL,
                element_type TEXT, element_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(project_id, element_id), FOREIGN KEY(project_id, page_id) REFERENCES html_pages(project_id, page_id)
            );
            CREATE TABLE IF NOT EXISTS html_observations (
                project_id TEXT NOT NULL, observation_id TEXT NOT NULL, page_id TEXT NOT NULL, element_id TEXT,
                action TEXT, result_json TEXT NOT NULL DEFAULT '{}', observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(project_id, observation_id), FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS requirement_element_links (
                project_id TEXT NOT NULL, link_id TEXT NOT NULL, indicator_id TEXT NOT NULL, page_id TEXT,
                confirmed_element_id TEXT, candidates_json TEXT NOT NULL DEFAULT '[]', confidence REAL NOT NULL DEFAULT 0,
                reason TEXT, status TEXT NOT NULL DEFAULT 'proposed', need_human_confirm INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(project_id, link_id),
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS case_indicator_links (
                project_id TEXT NOT NULL, case_id TEXT NOT NULL, indicator_id TEXT NOT NULL, case_version INTEGER NOT NULL DEFAULT 1,
                step_numbers_json TEXT NOT NULL DEFAULT '[]', coverage_type TEXT, coverage_status TEXT NOT NULL DEFAULT 'covered',
                PRIMARY KEY(project_id, case_id, indicator_id, case_version), FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS case_versions (
                project_id TEXT NOT NULL, case_id TEXT NOT NULL, version_no INTEGER NOT NULL, parent_version_no INTEGER,
                case_json TEXT NOT NULL, user_feedback TEXT, context_snapshot_json TEXT NOT NULL DEFAULT '{}', model_name TEXT,
                changed_fields_json TEXT NOT NULL DEFAULT '[]', acceptance_status TEXT NOT NULL DEFAULT 'proposed', operator TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(project_id, case_id, version_no),
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS case_conversations (
                project_id TEXT NOT NULL, conversation_id TEXT NOT NULL, case_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(project_id, conversation_id),
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS case_messages (
                project_id TEXT NOT NULL, message_id TEXT NOT NULL, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(project_id, message_id), FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS site_packages (
                project_id TEXT NOT NULL, site_package_id TEXT NOT NULL, filename TEXT NOT NULL,
                root_path TEXT NOT NULL, entry_path TEXT, manifest_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(project_id,site_package_id),
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS site_navigation_relations (
                project_id TEXT NOT NULL, site_package_id TEXT NOT NULL, relation_id TEXT NOT NULL,
                source_page TEXT NOT NULL, target TEXT NOT NULL, relation_type TEXT NOT NULL,
                relation_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(project_id,site_package_id,relation_id),
                FOREIGN KEY(project_id,site_package_id) REFERENCES site_packages(project_id,site_package_id)
            );
            CREATE TABLE IF NOT EXISTS requirement_page_links (
                project_id TEXT NOT NULL, link_id TEXT NOT NULL, function_id TEXT NOT NULL, page_id TEXT,
                confidence REAL NOT NULL DEFAULT 0, reason TEXT, status TEXT NOT NULL DEFAULT 'proposed',
                need_human_confirm INTEGER NOT NULL DEFAULT 1, model_name TEXT, evidence_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(project_id,link_id), FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS indicator_action_links (
                project_id TEXT NOT NULL, indicator_id TEXT NOT NULL, observation_id TEXT NOT NULL, action_id TEXT,
                PRIMARY KEY(project_id,indicator_id,observation_id,action_id), FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            CREATE TABLE IF NOT EXISTS case_page_links (project_id TEXT NOT NULL,case_id TEXT NOT NULL,page_id TEXT NOT NULL,PRIMARY KEY(project_id,case_id,page_id));
            CREATE TABLE IF NOT EXISTS case_element_links (project_id TEXT NOT NULL,case_id TEXT NOT NULL,element_id TEXT NOT NULL,PRIMARY KEY(project_id,case_id,element_id));
            CREATE TABLE IF NOT EXISTS case_observation_links (project_id TEXT NOT NULL,case_id TEXT NOT NULL,observation_id TEXT NOT NULL,PRIMARY KEY(project_id,case_id,observation_id));
            CREATE TABLE IF NOT EXISTS html_page_resources (
                project_id TEXT NOT NULL,page_id TEXT NOT NULL,resource_id TEXT NOT NULL,resource_type TEXT NOT NULL,
                reference TEXT NOT NULL DEFAULT '',summary TEXT NOT NULL DEFAULT '',missing INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(project_id,page_id,resource_id),FOREIGN KEY(project_id,page_id) REFERENCES html_pages(project_id,page_id)
            );
            CREATE TABLE IF NOT EXISTS html_page_summaries (
                project_id TEXT NOT NULL,page_id TEXT NOT NULL,summary_json TEXT NOT NULL DEFAULT '{}',
                source_bytes INTEGER NOT NULL DEFAULT 0,compressed_chars INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(project_id,page_id),FOREIGN KEY(project_id,page_id) REFERENCES html_pages(project_id,page_id)
            );
            CREATE TABLE IF NOT EXISTS equipment_import_errors (
                error_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_file TEXT NOT NULL,
                source_line_no INTEGER NOT NULL, error_type TEXT NOT NULL, error_message TEXT NOT NULL,
                raw_line TEXT, created_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(project_id)
            );
            """
        )
        cursor = conn.cursor()
        _ensure_columns(
            cursor, "requirement_nodes", {
                "section_number": "TEXT NOT NULL DEFAULT ''",
                "ancestor_node_ids_json": "TEXT NOT NULL DEFAULT '[]'", "ancestor_identifiers_json": "TEXT NOT NULL DEFAULT '[]'",
                "node_type": "TEXT NOT NULL DEFAULT 'group'", "source_position_json": "TEXT NOT NULL DEFAULT '{}'",
                "section_evidence_json": "TEXT NOT NULL DEFAULT '{}'", "overview_node_id": "TEXT NOT NULL DEFAULT ''",
                "testable": "INTEGER NOT NULL DEFAULT 0", "review_status": "TEXT NOT NULL DEFAULT 'pending'"
                ,"enabled": "INTEGER NOT NULL DEFAULT 1", "deleted_at": "TEXT", "generation_approved": "INTEGER NOT NULL DEFAULT 0",
                "extraction_method": "TEXT NOT NULL DEFAULT 'csci_structured'", "confidence": "REAL NOT NULL DEFAULT 0"
            }
        )
        _ensure_columns(
            cursor, "html_pages", {"site_package_id": "TEXT NOT NULL DEFAULT ''"}
        )
        _ensure_columns(
            cursor, "html_elements", {"site_package_id": "TEXT NOT NULL DEFAULT ''"}
        )
        _ensure_columns(
            cursor, "html_observations", {"site_package_id": "TEXT NOT NULL DEFAULT ''", "evidence_json": "TEXT NOT NULL DEFAULT '{}'", "migration_status": "TEXT NOT NULL DEFAULT ''"}
        )
        # Repair legacy path-shaped observation.page_id only inside the same project
        # and site package.  Ambiguous/unmatched rows remain untouched for review.
        legacy=list(cursor.execute("SELECT project_id,observation_id,page_id,site_package_id FROM html_observations WHERE page_id NOT LIKE 'PAGE-%'"))
        for project_id,observation_id,page_path,site_package_id in legacy:
            matches=list(cursor.execute("SELECT page_id FROM html_pages WHERE project_id=? AND site_package_id=? AND page_path=?",(project_id,site_package_id,page_path)))
            if len(matches)==1:
                cursor.execute("UPDATE html_observations SET page_id=?,migration_status='mapped_v22' WHERE project_id=? AND observation_id=?",(matches[0][0],project_id,observation_id))
            else:
                cursor.execute("UPDATE html_observations SET migration_status='needs_human_confirmation' WHERE project_id=? AND observation_id=?",(project_id,observation_id))
        _ensure_columns(
            cursor,
            "generation_runs",
            {
                "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            },
        )
        _ensure_columns(
            cursor,
            "project_requirements",
            {
                "requirement_type": "TEXT NOT NULL DEFAULT ''",
                "section_number": "TEXT NOT NULL DEFAULT ''",
                "section_path_json": "TEXT NOT NULL DEFAULT '[]'",
                "test_object": "TEXT NOT NULL DEFAULT ''",
                "actors_json": "TEXT NOT NULL DEFAULT '[]'",
                "preconditions_json": "TEXT NOT NULL DEFAULT '[]'",
                "inputs_json": "TEXT NOT NULL DEFAULT '[]'",
                "processing_rules_json": "TEXT NOT NULL DEFAULT '[]'",
                "outputs_json": "TEXT NOT NULL DEFAULT '[]'",
                "exception_rules_json": "TEXT NOT NULL DEFAULT '[]'",
                "performance_constraints_json": "TEXT NOT NULL DEFAULT '[]'",
                "interface_constraints_json": "TEXT NOT NULL DEFAULT '[]'",
                "security_constraints_json": "TEXT NOT NULL DEFAULT '[]'",
                "acceptance_criteria_json": "TEXT NOT NULL DEFAULT '[]'",
                "priority": "TEXT NOT NULL DEFAULT ''",
                "verification_method": "TEXT NOT NULL DEFAULT ''",
                "parent_requirement_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "recommended_test_type": "TEXT NOT NULL DEFAULT ''",
                "alternative_test_types_json": "TEXT NOT NULL DEFAULT '[]'",
                "test_type_confidence": "REAL NOT NULL DEFAULT 0",
                "test_type_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
                "source_chunk_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "source_documents_json": "TEXT NOT NULL DEFAULT '[]'",
                "source_evidence_json": "TEXT NOT NULL DEFAULT '[]'",
                "need_human_confirm": "INTEGER NOT NULL DEFAULT 0",
                "missing_information_json": "TEXT NOT NULL DEFAULT '[]'",
                "machine_extraction_json": "TEXT NOT NULL DEFAULT '{}'",
                "review_changes_json": "TEXT NOT NULL DEFAULT '{}'",
                "retained": "INTEGER NOT NULL DEFAULT 1",
            },
        )
        _ensure_columns(
            cursor,
            "project_chunks",
            {
                "page_no": "INTEGER",
                "source_type": "TEXT",
                "chunk_text": "TEXT",
                "page_start": "INTEGER",
                "page_end": "INTEGER",
                "parser_type": "TEXT",
                "chunk_level": "TEXT",
                "parent_section_id": "TEXT",
                "needs_ocr": "INTEGER NOT NULL DEFAULT 0",
                "parse_warning": "TEXT",
                "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            },
        )
        _ensure_columns(cursor, "feedback_candidates", {
            "candidate_key": "TEXT", "occurrence_count": "INTEGER NOT NULL DEFAULT 1",
        })
        _ensure_columns(cursor, "approved_learning_rules", {
            "revoked_by": "TEXT", "revoked_at": "TEXT", "revoke_reason": "TEXT",
        })
        _ensure_columns(
            cursor,
            "project_documents",
            {
                "file_hash": "TEXT",
                "parser_type": "TEXT",
                "parse_report_json": "TEXT NOT NULL DEFAULT '{}'",
                "processing_status": "TEXT NOT NULL DEFAULT 'pending'",
                "last_processed_page": "INTEGER NOT NULL DEFAULT 0",
            },
        )
        _ensure_columns(
            cursor,
            "generated_cases",
            {
                "case_type": "TEXT",
                "source_chunk_ids": "TEXT",
                "generation_run_id": "TEXT",
            },
        )
        _ensure_columns(
            cursor,
            "scenario_cards",
            {
                "compilation_status": "TEXT NOT NULL DEFAULT 'legacy'",
                "review_json": "TEXT NOT NULL DEFAULT '{}'",
            },
        )
        _ensure_columns(
            cursor,
            "scenario_validation_results",
            {
                "passed": "INTEGER NOT NULL DEFAULT 0",
                "score": "REAL NOT NULL DEFAULT 0",
                "blocking_issues_json": "TEXT NOT NULL DEFAULT '[]'",
                "missing_information_json": "TEXT NOT NULL DEFAULT '[]'",
                "check_results_json": "TEXT NOT NULL DEFAULT '{}'",
            },
        )
        _ensure_columns(
            cursor,
            "scenario_generation_runs",
            {
                "provenance_json": "TEXT NOT NULL DEFAULT '{}'",
                "step_trace_json": "TEXT NOT NULL DEFAULT '[]'",
            },
        )
        _ensure_columns(
            cursor,
            "scenario_equipment_allocations",
            {"source_refs_json": "TEXT NOT NULL DEFAULT '[]'"},
        )
        _ensure_columns(
            cursor,
            "learning_tasks",
            {
                "task_name": "TEXT NOT NULL DEFAULT ''",
                "learning_goal": "TEXT NOT NULL DEFAULT ''",
                "domain": "TEXT NOT NULL DEFAULT ''",
                "simulation_object": "TEXT NOT NULL DEFAULT ''",
                "target_subsystems_json": "TEXT NOT NULL DEFAULT '[]'",
                "target_topics_json": "TEXT NOT NULL DEFAULT '[]'",
                "expected_scenario_types_json": "TEXT NOT NULL DEFAULT '[]'",
                "excluded_topics_json": "TEXT NOT NULL DEFAULT '[]'",
                "coverage_candidates_json": "TEXT NOT NULL DEFAULT '[]'",
                "report_json": "TEXT NOT NULL DEFAULT '{}'",
            },
        )
        _ensure_columns(
            cursor,
            "knowledge_units",
            {
                "applicable_conditions_json": "TEXT NOT NULL DEFAULT '[]'",
                "inapplicable_conditions_json": "TEXT NOT NULL DEFAULT '[]'",
                "section_scope": "TEXT NOT NULL DEFAULT ''",
                "learning_task_id": "TEXT",
                "source_kind": "TEXT NOT NULL DEFAULT 'book'",
                "priority": "INTEGER NOT NULL DEFAULT 0",
                "approved_by": "TEXT",
                "approved_at": "TEXT",
            },
        )
        _ensure_columns(
            cursor,
            "knowledge_conflicts",
            {
                "conflict_key": "TEXT",
                "priority_winner_id": "TEXT",
            },
        )
        _ensure_columns(
            cursor,
            "knowledge_reviews",
            {
                "previous_status": "TEXT NOT NULL DEFAULT ''",
                "new_status": "TEXT NOT NULL DEFAULT ''",
                "action": "TEXT NOT NULL DEFAULT ''",
                "audit_json": "TEXT NOT NULL DEFAULT '{}'",
            },
        )
        _ensure_columns(
            cursor,
            "document_processing_jobs",
            {
                "current_page": "INTEGER NOT NULL DEFAULT 0",
                "total_pages": "INTEGER NOT NULL DEFAULT 0",
                "progress": "REAL NOT NULL DEFAULT 0",
                "error_message": "TEXT NOT NULL DEFAULT ''",
                "retry_count": "INTEGER NOT NULL DEFAULT 0",
                "lease_owner": "TEXT",
                "lease_token": "TEXT",
                "lease_expires_at": "TEXT",
                "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
                "idempotency_key": "TEXT",
            },
        )
        _ensure_columns(
            cursor,
            "scenario_cards",
            {
                "scenario_name": "TEXT",
                "scenario_type": "TEXT",
                "related_requirements": "TEXT",
                "actors": "TEXT",
                "trigger_event": "TEXT",
                "input_data": "TEXT",
                "system_state": "TEXT",
                "external_interfaces": "TEXT",
                "environment": "TEXT",
                "normal_flow": "TEXT",
                "abnormal_flow": "TEXT",
                "boundary_conditions": "TEXT",
                "performance_constraints": "TEXT",
                "safety_constraints": "TEXT",
                "source_document": "TEXT",
                "source_chunk_ids": "TEXT",
                "confidence": "REAL",
                "need_human_confirm": "INTEGER",
                "card_json": "TEXT",
            },
        )
        _ensure_columns(
            cursor,
            "equipment_entities",
            {
                "platform_type": "TEXT",
                "roles_json": "TEXT NOT NULL DEFAULT '[]'",
                "interfaces_json": "TEXT NOT NULL DEFAULT '[]'",
                "constraints_json": "TEXT NOT NULL DEFAULT '[]'",
                "simulation_parameters_json": "TEXT NOT NULL DEFAULT '{}'",
                "minimum_unit": "INTEGER",
                "maximum_unit": "INTEGER",
                "source_description": "TEXT",
                "source_file": "TEXT",
                "source_line_no": "INTEGER",
                "raw_payload_json": "TEXT NOT NULL DEFAULT '{}'",
                "record_hash": "TEXT",
            },
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_documents_project ON project_documents(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_chunks_project_document ON project_chunks(project_id, document_id)",
            "CREATE INDEX IF NOT EXISTS idx_chunks_document ON project_chunks(document_id)",
            "CREATE INDEX IF NOT EXISTS idx_embeddings_project_chunk ON project_chunk_embeddings(project_id, chunk_id, model_name)",
            "CREATE INDEX IF NOT EXISTS idx_assets_project_document ON project_assets(project_id, document_id)",
            "CREATE INDEX IF NOT EXISTS idx_visual_evidence_project_asset ON project_visual_evidence(project_id, asset_id)",
            "CREATE INDEX IF NOT EXISTS idx_requirements_project_id ON project_requirements(project_id, requirement_id)",
            "CREATE INDEX IF NOT EXISTS idx_scenarios_project_requirement ON scenario_cards(project_id, requirement_id)",
            "CREATE INDEX IF NOT EXISTS idx_scenarios_project_compilation_status ON scenario_cards(project_id, compilation_status, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_runs_project_id ON generation_runs(project_id, run_id)",
            "CREATE INDEX IF NOT EXISTS idx_cases_project_run ON generated_cases(project_id, generation_run_id)",
            "CREATE INDEX IF NOT EXISTS idx_cases_project_requirement ON generated_cases(project_id, requirement_id)",
            "CREATE INDEX IF NOT EXISTS idx_quality_project_case ON case_quality_scores(project_id, case_id)",
            "CREATE INDEX IF NOT EXISTS idx_reviews_project_case ON review_results(project_id, case_id)",
            "CREATE INDEX IF NOT EXISTS idx_traces_project_artifact ON trace_sources(project_id, artifact_id)",
            "CREATE INDEX IF NOT EXISTS idx_traces_project_chunk ON trace_sources(project_id, source_chunk_id)",
            "CREATE INDEX IF NOT EXISTS idx_learning_tasks_project_status ON learning_tasks(project_id, status, updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_knowledge_learning_task ON knowledge_units(project_id, learning_task_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_knowledge_generation_scope ON knowledge_units(project_id, status, section_scope, priority)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_conflict_key ON knowledge_conflicts(conflict_key) WHERE conflict_key IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_processing_jobs_project_document ON document_processing_jobs(project_id, document_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_processing_jobs_claim ON document_processing_jobs(status, lease_expires_at, created_at)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_processing_jobs_idempotency ON document_processing_jobs(idempotency_key) WHERE idempotency_key IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_sections_project_document_sequence ON document_sections(project_id, document_id, sequence_no)",
            "CREATE INDEX IF NOT EXISTS idx_sections_project_chunk ON document_sections(project_id, chunk_id)",
            "CREATE INDEX IF NOT EXISTS idx_knowledge_project_type ON knowledge_units(project_id, knowledge_type, status)",
            "CREATE INDEX IF NOT EXISTS idx_knowledge_project_document ON knowledge_units(project_id, document_id, page_no)",
            "CREATE INDEX IF NOT EXISTS idx_knowledge_project_chunk ON knowledge_units(project_id, chunk_id)",
            "CREATE INDEX IF NOT EXISTS idx_relations_project_source ON knowledge_relations(project_id, source_knowledge_unit_id, relation_type)",
            "CREATE INDEX IF NOT EXISTS idx_conflicts_project_status ON knowledge_conflicts(project_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_knowledge_reviews_project_unit ON knowledge_reviews(project_id, knowledge_unit_id, reviewed_at)",
            "CREATE INDEX IF NOT EXISTS idx_equipment_project_name ON equipment_entities(project_id, name)",
            "CREATE INDEX IF NOT EXISTS idx_equipment_project_type ON equipment_entities(project_id, category, equipment_type)",
            "CREATE INDEX IF NOT EXISTS idx_equipment_jsonl_source ON equipment_entities(project_id, jsonl_record_no)",
            "CREATE INDEX IF NOT EXISTS idx_aliases_project_alias ON equipment_aliases(project_id, alias)",
            "CREATE INDEX IF NOT EXISTS idx_capabilities_project_equipment ON equipment_capabilities(project_id, equipment_id, capability_type)",
            "CREATE INDEX IF NOT EXISTS idx_roles_project_role ON equipment_role_mappings(project_id, role_name, priority)",
            "CREATE INDEX IF NOT EXISTS idx_config_rules_project_enabled ON equipment_configuration_rules(project_id, enabled)",
            "CREATE INDEX IF NOT EXISTS idx_inventory_project_equipment ON project_equipment_inventory(project_id, equipment_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_templates_project_category ON scenario_templates(project_id, scenario_category, status)",
            "CREATE INDEX IF NOT EXISTS idx_scenario_runs_project_scenario ON scenario_generation_runs(project_id, scenario_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_allocations_project_scenario ON scenario_equipment_allocations(project_id, scenario_id)",
            "CREATE INDEX IF NOT EXISTS idx_allocations_project_equipment ON scenario_equipment_allocations(project_id, equipment_id)",
            "CREATE INDEX IF NOT EXISTS idx_validations_project_scenario ON scenario_validation_results(project_id, scenario_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_feedback_project_status ON feedback_candidates(project_id, status, candidate_type)",
            "CREATE INDEX IF NOT EXISTS idx_learning_rules_project_type ON approved_learning_rules(project_id, rule_type, enabled)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_candidate_key ON feedback_candidates(project_id, candidate_key)",
            "CREATE INDEX IF NOT EXISTS idx_feedback_corrections_project ON feedback_corrections(project_id, status, entity_type, field_name)",
            "CREATE INDEX IF NOT EXISTS idx_feedback_rule_audit_project ON feedback_rule_audit(project_id, learning_rule_id, created_at)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_equipment_project_record_hash ON equipment_entities(project_id, record_hash) WHERE record_hash IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_equipment_project_platform ON equipment_entities(project_id, platform_type)",
            "CREATE INDEX IF NOT EXISTS idx_equipment_import_errors_project_source ON equipment_import_errors(project_id, source_file, source_line_no)",
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_project_file_hash ON project_documents(project_id, file_hash) WHERE file_hash IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_chunks_project_parent ON project_chunks(project_id, parent_section_id, chunk_level)",
            "CREATE INDEX IF NOT EXISTS idx_documents_project_status ON project_documents(project_id, processing_status)",
            "CREATE INDEX IF NOT EXISTS idx_requirement_nodes_project_parent ON requirement_nodes(project_id, parent_id, level)",
            "CREATE INDEX IF NOT EXISTS idx_indicators_project_function ON requirement_indicators(project_id, function_id)",
            "CREATE INDEX IF NOT EXISTS idx_html_elements_project_page ON html_elements(project_id, page_id)",
            "CREATE INDEX IF NOT EXISTS idx_requirement_links_project_indicator ON requirement_element_links(project_id, indicator_id)",
            "CREATE INDEX IF NOT EXISTS idx_case_versions_project_case ON case_versions(project_id, case_id, version_no)",
            "CREATE INDEX IF NOT EXISTS idx_site_packages_project ON site_packages(project_id,site_package_id)",
            "CREATE INDEX IF NOT EXISTS idx_site_navigation_project ON site_navigation_relations(project_id,site_package_id)",
        ):
            cursor.execute(statement)
        cursor.execute(
            "INSERT OR IGNORE INTO schema_versions(version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
