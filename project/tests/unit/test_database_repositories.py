from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import core.project_manager as manager_module
from core.project_manager import ProjectManager
from domain.exceptions import PersistenceError
from infrastructure.db.connection import SQLiteConnectionManager
from infrastructure.db.migrations import SCHEMA_VERSION, migrate_database


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectManager:
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    return ProjectManager(tmp_path / "workspace.db")


def _tables(manager: ProjectManager) -> set[str]:
    with manager.connections.connection() as conn:
        return {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def test_empty_database_initialization_and_indexes(manager: ProjectManager) -> None:
    assert {
        "projects",
        "project_documents",
        "project_chunks",
        "schema_versions",
    } <= _tables(manager)
    with manager.connections.connection() as conn:
        assert (
            conn.execute("SELECT version FROM schema_versions").fetchone()[0]
            == SCHEMA_VERSION
        )
        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
    assert {
        "idx_chunks_project_document",
        "idx_cases_project_run",
        "idx_traces_project_chunk",
    } <= indexes


def test_old_database_upgrade_is_additive_and_preserves_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE projects(project_id TEXT PRIMARY KEY, project_name TEXT NOT NULL, description TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE generated_cases(case_id TEXT NOT NULL, project_id TEXT NOT NULL, requirement_id TEXT, case_json TEXT, created_at TEXT NOT NULL, PRIMARY KEY(project_id, case_id));
        INSERT INTO projects VALUES('P-OLD','旧项目','保留','2020','2020');
        INSERT INTO generated_cases VALUES('C-OLD','P-OLD','R-OLD','{}','2020');
    """)
    conn.commit()
    conn.close()
    connections = SQLiteConnectionManager(db_path)
    migrate_database(connections)
    with connections.connection() as upgraded:
        assert upgraded.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert (
            upgraded.execute("SELECT COUNT(*) FROM generated_cases").fetchone()[0] == 1
        )
        columns = {
            row[1] for row in upgraded.execute("PRAGMA table_info(generated_cases)")
        }
    assert {"case_type", "source_chunk_ids", "generation_run_id"} <= columns


def test_migration_can_run_repeatedly_without_data_changes(
    manager: ProjectManager,
) -> None:
    project = manager.create_project("幂等迁移")
    migrate_database(manager.connections)
    migrate_database(manager.connections)
    assert manager.get_project(project["project_id"])["project_name"] == "幂等迁移"
    with manager.connections.connection() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM schema_versions WHERE version=?",
                (SCHEMA_VERSION,),
            ).fetchone()[0]
            == 1
        )


def test_transaction_rolls_back(manager: ProjectManager) -> None:
    with pytest.raises(RuntimeError):
        with manager.connections.transaction() as conn:
            conn.execute(
                "INSERT INTO projects VALUES('P-ROLLBACK','回滚','','2020','2020')"
            )
            raise RuntimeError("abort")
    assert manager.get_project("P-ROLLBACK") is None


def test_foreign_keys_are_enabled_and_errors_are_translated(
    manager: ProjectManager,
) -> None:
    with manager.connections.connection() as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(PersistenceError):
        with manager.connections.transaction() as conn:
            conn.execute(
                "INSERT INTO project_documents VALUES('D-BAD','P-MISSING','x','txt','x','2020')"
            )


def test_crud_and_multi_project_isolation(
    manager: ProjectManager, tmp_path: Path
) -> None:
    first = manager.create_project("项目 A")
    second = manager.create_project("项目 B")
    first_id, second_id = first["project_id"], second["project_id"]
    path = tmp_path / "requirements.txt"
    path.write_text("需求", encoding="utf-8")
    document_id = manager.add_document(first_id, path.name, "txt", path)
    chunk_id = manager.replace_chunks(
        first_id, document_id, [{"text": "仅属于项目 A", "metadata": {"page_no": 1}}]
    )[0]
    manager.save_profile(
        first_id, {"project_name": "项目 A", "main_functions": ["功能"]}
    )
    manager.upsert_requirement(first_id, {"requirement_id": "REQ-1", "title": "需求"})
    manager.replace_full_scenario_cards(
        first_id,
        [
            {
                "scenario_id": "SCN-1",
                "scenario_name": "场景",
                "related_requirements": ["REQ-1"],
                "source_chunk_ids": [chunk_id],
            }
        ],
    )
    run_id = manager.create_generation_run(first_id, "test")
    manager.save_generated_case(
        first_id,
        {
            "case_id": "CASE-1",
            "requirement_id": "REQ-1",
            "source_chunk_ids": [chunk_id],
        },
        run_id,
        manager.list_chunks(first_id),
    )
    manager.save_quality_score(first_id, "CASE-1", "", {"score": 90})
    manager.save_review_result(first_id, "CASE-1", "规则", "通过", [])
    assert manager.get_profile(first_id)
    assert manager.get_requirement(first_id, "REQ-1")
    assert manager.list_scenario_cards(first_id)
    assert manager.list_generated_cases(first_id)
    assert manager.list_trace_sources(first_id)
    assert manager.list_quality_scores(first_id)
    assert manager.list_review_results(first_id)
    for getter in (
        manager.list_documents,
        manager.list_chunks,
        manager.list_requirements,
        manager.list_scenario_cards,
        manager.list_generated_cases,
        manager.list_trace_sources,
    ):
        assert getter(second_id) == []


def test_legacy_project_manager_connection_and_methods_remain_compatible(
    manager: ProjectManager,
) -> None:
    project = manager.create_project("兼容入口", "说明")
    assert manager.list_projects()[0]["project_id"] == project["project_id"]
    connection = manager._connect()
    try:
        assert connection.row_factory is sqlite3.Row
        assert connection.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
    finally:
        connection.close()
