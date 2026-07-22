from pathlib import Path

import pytest

import application.services.project_service as manager_module
from application.services.project_service import ProjectManager
from workflows.learning.feedback_learning_service import FeedbackLearningService
from workflows.scenario.scenario_workflow import ScenarioWorkflow


@pytest.fixture
def feedback_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    manager = ProjectManager(tmp_path / "feedback.db")
    first = manager.create_project("first")["project_id"]
    second = manager.create_project("second")["project_id"]
    return manager, first, second, FeedbackLearningService(manager)


def correction(project_id: str, **updates):
    value = {
        "original_value": ["old"],
        "corrected_value": ["new"],
        "field_name": "normal_flow",
        "entity_type": "scenario",
        "correction_reason": "formal review",
        "project_id": project_id,
        "scenario_id": "SCN-1",
        "requirement_ids": ["REQ-1"],
        "source_context": {
            "condition": {"mission_phase": "cruise"},
            "document_id": "DOC-1", "chunk_id": "CHK-1", "page_no": 3,
            "conversation_history": ["must not be persisted"],
        },
        "created_by": "reviewer",
    }
    value.update(updates)
    return value


def test_repeated_corrections_aggregate_pending_candidate(feedback_workspace) -> None:
    manager, project_id, _, service = feedback_workspace
    first = service.record_correction(correction(project_id))
    second = service.record_correction(correction(project_id))
    assert first["candidate_id"] == second["candidate_id"]
    candidate = service.list_candidates(project_id, "pending")[0]
    assert candidate["occurrence_count"] == 2
    assert candidate["feedback"]["evidence_count"] == 2
    with manager.connections.connection() as conn:
        row = conn.execute("SELECT * FROM feedback_corrections LIMIT 1").fetchone()
        assert "conversation_history" not in row["source_context_json"]
        assert row["status"] == "candidate"


def test_only_approved_rules_match_and_projects_are_isolated(feedback_workspace) -> None:
    _, project_id, other_project, service = feedback_workspace
    candidate = service.record_correction(correction(project_id))
    context = {"mission_phase": "cruise"}
    assert service.match_approved_rules(project_id, context) == []
    approved = service.approve_candidate(
        project_id, candidate["candidate_id"], approved_by="approver"
    )
    matches = service.match_approved_rules(project_id, context)
    assert [item["learning_rule_id"] for item in matches] == [approved["learning_rule_id"]]
    assert matches[0]["match_reason"] == "mission_phase=cruise"
    assert service.match_approved_rules(other_project, context) == []


def test_global_promotion_is_explicit_and_numeric_is_never_promoted(feedback_workspace) -> None:
    _, project_id, other_project, service = feedback_workspace
    local = service.record_correction(correction(project_id))
    service.approve_candidate(
        project_id, local["candidate_id"], approved_by="org-admin", promote_to_global=True
    )
    context = {"mission_phase": "cruise"}
    assert service.match_approved_rules(other_project, context) == []
    assert len(service.match_approved_rules(other_project, context, allow_global=True)) == 1

    numeric = service.record_correction(correction(
        project_id, field_name="threshold", original_value=3,
        corrected_value=4, candidate_type="parameter_usage_rule",
    ))
    with pytest.raises(ValueError, match="numeric parameter"):
        service.approve_candidate(
            project_id, numeric["candidate_id"], approved_by="org-admin",
            promote_to_global=True,
        )


def test_revoked_rule_no_longer_matches_but_keeps_audit(feedback_workspace) -> None:
    manager, project_id, _, service = feedback_workspace
    candidate = service.record_correction(correction(project_id))
    rule = service.approve_candidate(
        project_id, candidate["candidate_id"], approved_by="approver"
    )
    service.revoke_rule(
        project_id, rule["learning_rule_id"], revoked_by="approver", reason="superseded"
    )
    assert service.match_approved_rules(project_id, {"mission_phase": "cruise"}) == []
    assert service.explain_generation(project_id, [rule["learning_rule_id"]]) == []
    with manager.connections.connection() as conn:
        actions = [row[0] for row in conn.execute(
            "SELECT action FROM feedback_rule_audit WHERE learning_rule_id=? ORDER BY created_at,audit_id",
            (rule["learning_rule_id"],),
        )]
    assert set(actions) == {"approved", "revoked"}


def test_scenario_generation_returns_and_applies_matched_rule_id(feedback_workspace) -> None:
    _, project_id, _, service = feedback_workspace
    value = correction(project_id)
    value["source_context"] = {"condition": {}}
    value["corrected_value"] = ["approved-feedback-step"]
    candidate = service.record_correction(value)
    rule = service.approve_candidate(
        project_id, candidate["candidate_id"], approved_by="approver"
    )
    result = ScenarioWorkflow(service.manager).run(project_id, {
        "scenario_goal": "verify feedback",
        "simulation_object": "simulator",
        "target_subsystem": "controller",
        "mission_phase": "cruise",
        "scale": 1,
        "focus_risks": [],
        "use_project_defaults": False,
    })
    assert result.provenance["feedback_rule_ids"] == [rule["learning_rule_id"]]
    assert "approved-feedback-step" in result.scenarios[0].normal_flow
