"""Additive, idempotent migrations for the project workspace database."""

from __future__ import annotations

import re
import sqlite3
from typing import Dict

from infrastructure.db.connection import SQLiteConnectionManager

SCHEMA_VERSION = 1
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
            """
        )
        cursor = conn.cursor()
        _ensure_columns(
            cursor,
            "project_chunks",
            {"page_no": "INTEGER", "source_type": "TEXT", "chunk_text": "TEXT"},
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
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_documents_project ON project_documents(project_id)",
            "CREATE INDEX IF NOT EXISTS idx_chunks_project_document ON project_chunks(project_id, document_id)",
            "CREATE INDEX IF NOT EXISTS idx_chunks_document ON project_chunks(document_id)",
            "CREATE INDEX IF NOT EXISTS idx_embeddings_project_chunk ON project_chunk_embeddings(project_id, chunk_id, model_name)",
            "CREATE INDEX IF NOT EXISTS idx_assets_project_document ON project_assets(project_id, document_id)",
            "CREATE INDEX IF NOT EXISTS idx_visual_evidence_project_asset ON project_visual_evidence(project_id, asset_id)",
            "CREATE INDEX IF NOT EXISTS idx_requirements_project_id ON project_requirements(project_id, requirement_id)",
            "CREATE INDEX IF NOT EXISTS idx_scenarios_project_requirement ON scenario_cards(project_id, requirement_id)",
            "CREATE INDEX IF NOT EXISTS idx_runs_project_id ON generation_runs(project_id, run_id)",
            "CREATE INDEX IF NOT EXISTS idx_cases_project_run ON generated_cases(project_id, generation_run_id)",
            "CREATE INDEX IF NOT EXISTS idx_cases_project_requirement ON generated_cases(project_id, requirement_id)",
            "CREATE INDEX IF NOT EXISTS idx_quality_project_case ON case_quality_scores(project_id, case_id)",
            "CREATE INDEX IF NOT EXISTS idx_reviews_project_case ON review_results(project_id, case_id)",
            "CREATE INDEX IF NOT EXISTS idx_traces_project_artifact ON trace_sources(project_id, artifact_id)",
            "CREATE INDEX IF NOT EXISTS idx_traces_project_chunk ON trace_sources(project_id, source_chunk_id)",
        ):
            cursor.execute(statement)
        cursor.execute(
            "INSERT OR IGNORE INTO schema_versions(version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
