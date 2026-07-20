"""Database migration tests for compiled scenarios, equipment, and learning."""

from pathlib import Path

import pytest

import core.project_manager as manager_module
from core.project_manager import ProjectManager
from infrastructure.db.json_codec import dumps_json, loads_json
from infrastructure.db.migrations import SCHEMA_VERSION, migrate_database


NEW_TABLES = {
    "learning_tasks",
    "document_processing_jobs",
    "document_sections",
    "knowledge_units",
    "knowledge_relations",
    "knowledge_conflicts",
    "knowledge_reviews",
    "equipment_entities",
    "equipment_aliases",
    "equipment_capabilities",
    "equipment_role_mappings",
    "equipment_configuration_rules",
    "project_equipment_inventory",
    "scenario_templates",
    "scenario_generation_runs",
    "scenario_equipment_allocations",
    "scenario_validation_results",
    "feedback_candidates",
    "approved_learning_rules",
    "feedback_corrections",
    "feedback_rule_audit",
}

SOURCE_FACT_TABLES = {
    "document_sections",
    "knowledge_units",
    "knowledge_relations",
    "knowledge_conflicts",
    "knowledge_reviews",
    "equipment_entities",
    "equipment_aliases",
    "equipment_capabilities",
    "equipment_role_mappings",
    "equipment_configuration_rules",
    "project_equipment_inventory",
    "scenario_templates",
    "scenario_generation_runs",
    "scenario_equipment_allocations",
    "scenario_validation_results",
    "feedback_candidates",
    "approved_learning_rules",
    "feedback_corrections",
    "feedback_rule_audit",
}


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectManager:
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    return ProjectManager(tmp_path / "workspace.db")


def _columns(conn, table: str) -> dict[str, str]:
    return {row[1]: row[2].upper() for row in conn.execute(f"PRAGMA table_info({table})")}


def test_v2_migration_creates_project_scoped_traceable_tables(manager: ProjectManager) -> None:
    with manager.connections.connection() as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert NEW_TABLES <= tables
        for table in NEW_TABLES:
            assert "project_id" in _columns(conn, table)
        for table in SOURCE_FACT_TABLES:
            columns = _columns(conn, table)
            assert {"document_id", "chunk_id", "page_no", "jsonl_record_no"} <= set(
                columns
            )
        assert conn.execute("SELECT MAX(version) FROM schema_versions").fetchone()[0] == SCHEMA_VERSION


def test_new_json_columns_are_text_and_indexes_exist(manager: ProjectManager) -> None:
    with manager.connections.connection() as conn:
        for table in NEW_TABLES:
            columns = _columns(conn, table)
            assert all(
                declared_type == "TEXT"
                for name, declared_type in columns.items()
                if name.endswith("_json")
            )
        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert {
        "idx_learning_tasks_project_status",
        "idx_knowledge_project_type",
        "idx_equipment_project_name",
        "idx_allocations_project_scenario",
        "idx_validations_project_scenario",
    } <= indexes


def test_v2_migration_is_repeatable_and_preserves_existing_rows(manager: ProjectManager) -> None:
    project = manager.create_project("兼容项目")
    migrate_database(manager.connections)
    migrate_database(manager.connections)
    assert manager.get_project(project["project_id"])["project_name"] == "兼容项目"
    with manager.connections.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_versions WHERE version=?", (SCHEMA_VERSION,)
        ).fetchone()[0] == 1


def test_central_json_codec_round_trip_and_safe_default() -> None:
    payload = {"中文": ["值"], "nested": {"enabled": True}}
    encoded = dumps_json(payload)
    assert isinstance(encoded, str)
    assert "中文" in encoded
    assert loads_json(encoded, {}) == payload
    assert loads_json("not-json", []) == []
