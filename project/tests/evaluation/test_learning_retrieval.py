from pathlib import Path

import pytest

import application.services.project_service as manager_module
from application.services.project_service import ProjectManager
from infrastructure.database.json_codec import dumps_json
from infrastructure.repositories.base import now_iso
from workflows.learning.knowledge_review_service import KnowledgeReviewService


@pytest.fixture
def learning_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    manager = ProjectManager(tmp_path / "evaluation.db")
    project = manager.create_project("evaluation")['project_id']
    other = manager.create_project("other")['project_id']
    timestamp = now_iso()
    rows = [
        ("K-PROJECT", project, "speed", 80, "formal_document", "approved", "chapter-project"),
        ("K-BOOK", project, "speed", 120, "book", "reviewed", "chapter-book"),
        ("K-X-A", project, "x", 1, "formal_document", "approved", "chapter-a"),
        ("K-X-B", project, "x", 2, "formal_document", "approved", "chapter-b"),
        ("K-REJECTED", project, "bad", 99, "book", "rejected", "chapter-bad"),
        ("K-OTHER", other, "secret", 7, "formal_document", "approved", "other"),
        ("K-GLOBAL", "GLOBAL", "shared", 1, "formal_document", "approved", "global"),
    ]
    with manager.connections.transaction() as conn:
        conn.execute(
            """INSERT INTO projects(project_id,project_name,description,created_at,updated_at)
            VALUES('GLOBAL','Organization shared scope','evaluation fixture',?,?)""",
            (timestamp, timestamp),
        )
        for unit_id, scope, name, value, source_kind, status, section in rows:
            conn.execute(
                """INSERT INTO knowledge_units(
                knowledge_unit_id,project_id,knowledge_type,title,content,
                normalized_data_json,tags_json,confidence,need_human_confirm,status,
                created_at,updated_at,source_kind,priority,section_scope)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (unit_id, scope, "parameter", name, f"{name}={value}",
                 dumps_json({"name": name, "symbol": name, "value": value,
                             "unit": "m/s", "operating_condition": section}),
                 "[]", 1.0, 0, status, timestamp, timestamp, source_kind, 500, section),
            )
    return KnowledgeReviewService(manager), project


def test_project_approved_parameter_wins_and_book_cannot_override(learning_scope) -> None:
    service, project = learning_scope
    rows = service.query_for_scenario(project)
    by_id = {row["knowledge_unit_id"]: row for row in rows}
    assert by_id["K-PROJECT"]["normalized_data"]["value"] == 80
    assert "K-BOOK" not in by_id
    assert "K-REJECTED" not in by_id


def test_same_symbol_keeps_section_scope(learning_scope) -> None:
    service, project = learning_scope
    rows = [row for row in service.query_for_scenario(project) if row["title"] == "x"]
    assert {(row["section_scope"], row["normalized_data"]["value"]) for row in rows} == {
        ("chapter-a", 1), ("chapter-b", 2),
    }


def test_other_project_never_leaks_and_global_is_explicit(learning_scope) -> None:
    service, project = learning_scope
    local = {row["knowledge_unit_id"] for row in service.query_for_scenario(project)}
    allowed = {row["knowledge_unit_id"] for row in service.query_for_scenario(
        project, include_global=True
    )}
    assert "K-OTHER" not in local and "K-OTHER" not in allowed
    assert "K-GLOBAL" not in local
    assert "K-GLOBAL" in allowed
