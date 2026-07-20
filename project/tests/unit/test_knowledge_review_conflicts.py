from pathlib import Path
from typing import Any

import pytest

import core.project_manager as manager_module
from core.project_manager import ProjectManager
from infrastructure.db.json_codec import dumps_json
from infrastructure.db.repositories.base import now_iso
from learning.knowledge_conflict_detector import KnowledgeConflictDetector
from learning.knowledge_review_service import KnowledgeReviewService


@pytest.fixture
def knowledge_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    manager = ProjectManager(tmp_path / "knowledge.db")
    project_id = manager.create_project("review project")["project_id"]
    with manager.connections.transaction() as conn:
        timestamp = now_iso()
        conn.execute(
            "INSERT INTO projects VALUES('GLOBAL','Organization','',?,?)",
            (timestamp, timestamp),
        )
    return manager, project_id


def add_unit(manager: Any, project_id: str, unit_id: str, *, title: str,
             content: str, status: str = "reviewed", source_kind: str = "book",
             knowledge_type: str = "constraint", data: dict | None = None,
             document_id: str = "DOC", chunk_id: str = "CHK", scope: str = "") -> None:
    timestamp = now_iso()
    with manager.connections.transaction() as conn:
        conn.execute(
            """INSERT INTO knowledge_units(
            knowledge_unit_id,project_id,knowledge_type,title,content,
            normalized_data_json,tags_json,document_id,chunk_id,confidence,
            need_human_confirm,status,created_at,updated_at,
            applicable_conditions_json,inapplicable_conditions_json,section_scope,
            source_kind,priority) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (unit_id, project_id, knowledge_type, title, content,
             dumps_json(data or {}), "[]", document_id, chunk_id, 0.9, 0, status,
             timestamp, timestamp, "[]", "[]", scope, source_kind, 0),
        )


def test_scenario_query_obeys_priority_status_and_scope(knowledge_db) -> None:
    manager, project_id = knowledge_db
    add_unit(manager, project_id, "K-PROJECT", title="A", content="a", status="approved")
    add_unit(manager, project_id, "K-DOC", title="B", content="b", source_kind="formal_document")
    add_unit(manager, project_id, "K-FEEDBACK", title="C", content="c", source_kind="human_feedback")
    add_unit(manager, "GLOBAL", "K-TEMPLATE", title="D", content="d", status="approved", source_kind="organization_template")
    add_unit(manager, project_id, "K-BOOK", title="E", content="e", source_kind="book", scope="SEC-1")
    add_unit(manager, project_id, "K-HISTORY", title="F", content="f", source_kind="historical_case", scope="SEC-2")
    add_unit(manager, project_id, "K-REJECT", title="G", content="g", status="rejected")
    add_unit(manager, project_id, "K-DEPRECATED", title="H", content="h", status="deprecated")
    add_unit(manager, project_id, "K-DRAFT", title="I", content="i", status="draft")
    service = KnowledgeReviewService(manager)
    rows = service.query_for_scenario(project_id, include_global=True)
    assert [row["knowledge_unit_id"] for row in rows] == [
        "K-PROJECT", "K-DOC", "K-FEEDBACK", "K-TEMPLATE", "K-BOOK", "K-HISTORY"
    ]
    scoped = service.query_for_scenario(project_id, section_scope="SEC-1", include_global=True)
    assert "K-BOOK" in {row["knowledge_unit_id"] for row in scoped}
    assert "K-HISTORY" not in {row["knowledge_unit_id"] for row in scoped}


def test_conflicts_retain_versions_and_approved_project_wins(knowledge_db) -> None:
    manager, project_id = knowledge_db
    add_unit(manager, project_id, "K-APPROVED", title="最大速度", content="最大速度为300",
             status="approved", source_kind="formal_document", knowledge_type="parameter",
             data={"name": "最大速度", "symbol": "Vmax", "value": 300, "unit": "km/h",
                   "operating_condition": "标准状态"}, scope="性能")
    add_unit(manager, project_id, "K-BOOK-NEW", title="最大速度", content="最大速度为280",
             status="draft", source_kind="book", knowledge_type="parameter",
             data={"name": "最大速度", "symbol": "Vmax", "value": 280, "unit": "",
                   "operating_condition": "低温状态"}, scope="性能")
    conflicts = KnowledgeConflictDetector(manager).detect(project_id)
    kinds = {row["conflict_type"] for row in conflicts}
    assert {"same_parameter_different_value", "missing_unit",
            "project_document_vs_book", "approved_rule_vs_new_candidate"} <= kinds
    assert all(
        row["priority_winner_id"] == "K-APPROVED"
        for row in conflicts if len(row["knowledge_unit_ids"]) == 2
    )
    with manager.connections.connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM knowledge_units WHERE knowledge_unit_id IN ('K-APPROVED','K-BOOK-NEW')"
        ).fetchone()[0] == 2


def test_same_symbol_conflict_is_limited_to_same_section_scope(knowledge_db) -> None:
    manager, project_id = knowledge_db
    common = {"symbol": "h", "value": 1, "unit": "m"}
    add_unit(manager, project_id, "K-HEIGHT", title="高度", content="h为高度",
             knowledge_type="parameter", data=common | {"name": "高度"}, scope="动力学")
    add_unit(manager, project_id, "K-STEP", title="步长", content="h为步长",
             knowledge_type="parameter", data=common | {"name": "步长"}, scope="动力学")
    add_unit(manager, project_id, "K-OTHER", title="湿度", content="h为湿度",
             knowledge_type="parameter", data=common | {"name": "湿度"}, scope="环境")
    conflicts = KnowledgeConflictDetector(manager).detect(project_id)
    symbol_pairs = [set(row["knowledge_unit_ids"]) for row in conflicts
                    if row["conflict_type"] == "same_symbol_different_meaning"]
    assert {"K-HEIGHT", "K-STEP"} in symbol_pairs
    assert all("K-OTHER" not in pair for pair in symbol_pairs)


def test_review_writes_audit_and_controls_generation_visibility(knowledge_db) -> None:
    manager, project_id = knowledge_db
    add_unit(manager, project_id, "K-REVIEW", title="限制", content="初始限制", status="draft")
    service = KnowledgeReviewService(manager)
    audit = service.review(
        project_id, "K-REVIEW", new_status="approved", reviewer="reviewer-a",
        comments=["来源已核实"], corrections={"verified": True},
    )
    assert audit["previous_status"] == "draft"
    assert audit["new_status"] == "approved"
    assert service.list_audit_log(project_id, "K-REVIEW")[0]["audit"]["reviewer"] == "reviewer-a"
    assert "K-REVIEW" in {row["knowledge_unit_id"] for row in service.query_for_scenario(project_id)}
    service.review(project_id, "K-REVIEW", new_status="deprecated", reviewer="reviewer-b")
    assert "K-REVIEW" not in {row["knowledge_unit_id"] for row in service.query_for_scenario(project_id)}
    assert len(service.list_audit_log(project_id, "K-REVIEW")) == 2
