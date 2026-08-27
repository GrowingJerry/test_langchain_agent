from infrastructure.database.connection import SQLiteConnectionManager
from infrastructure.database.migrations import migrate_database
from infrastructure.repositories.traceability_repository import TraceabilityRepository


def test_migration_is_idempotent_and_versions_are_project_scoped(tmp_path):
    connections = SQLiteConnectionManager(tmp_path / "workspace.db")
    migrate_database(connections)
    migrate_database(connections)
    with connections.transaction() as conn:
        for project_id in ("P1", "P2"):
            conn.execute(
                "INSERT INTO projects(project_id,project_name,created_at,updated_at) VALUES(?,?,?,?)",
                (project_id, project_id, "2026-01-01", "2026-01-01"),
            )
    repository = TraceabilityRepository(connections)
    v1 = repository.create_case_version("P1", "TC-1", {"steps": ["a"], "expected": ["b"]})
    v2 = repository.create_case_version(
        "P1", "TC-1", {"steps": ["a2"], "expected": ["b"]}, feedback="只修改步骤"
    )
    repository.create_case_version("P2", "TC-1", {"steps": ["other"], "expected": ["other"]})
    assert v1["version_no"] == 1 and v2["version_no"] == 2
    assert v2["changed_fields"] == ["test_steps"]
    assert len(repository.list_case_versions("P1", "TC-1")) == 2
    assert len(repository.list_case_versions("P2", "TC-1")) == 1
    repository.set_version_status("P1", "TC-1", 2, "accepted")
    assert repository.list_case_versions("P1", "TC-1")[-1]["acceptance_status"] == "accepted"
