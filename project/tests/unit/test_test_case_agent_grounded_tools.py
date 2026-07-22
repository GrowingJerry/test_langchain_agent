from pathlib import Path
from typing import Any, Sequence

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

import application.services.project_service as manager_module
from agents.test_case.agent import TestCaseAgent
from agents.test_case.context import AgentRuntimeContext
from agents.test_case.output_schema import GeneratedCaseBundle, TestCaseAgentRequest
from agents.test_case.tools import build_test_case_tools
from config.settings import Settings
from application.services.project_service import ProjectManager
from domain.schemas.test_case import TestCase
from infrastructure.database.json_codec import dumps_json
from infrastructure.repositories.base import now_iso


class BindableModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: Sequence[Any], **kwargs: Any):
        return self


@pytest.fixture
def grounded_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    manager = ProjectManager(tmp_path / "agent-tools.db")
    project_id = manager.create_project("P1")["project_id"]
    other_id = manager.create_project("P2")["project_id"]
    manager.equipment.ensure_global_project()
    timestamp = now_iso()
    with manager.connections.transaction() as conn:
        for scope, suffix in ((project_id, "LOCAL"), (other_id, "OTHER"), ("GLOBAL", "GLOBAL")):
            for kind in ("simulation_model", "state_transition", "parameter"):
                data = ({"name": "速度", "value": 10, "unit": "m/s",
                         "operating_condition": "正常状态"} if kind == "parameter" else {"name": suffix})
                conn.execute(
                    """INSERT INTO knowledge_units(
                    knowledge_unit_id,project_id,knowledge_type,title,content,
                    normalized_data_json,tags_json,document_id,chunk_id,confidence,
                    need_human_confirm,status,created_at,updated_at,source_kind,priority,
                    applicable_conditions_json,inapplicable_conditions_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"K-{kind}-{suffix}", scope, kind, f"飞控 {kind} {suffix}",
                     f"飞控 {kind} {suffix}", dumps_json(data), "[]", f"DOC-{suffix}",
                     f"CHK-{suffix}", 1.0, 0, "approved", timestamp, timestamp,
                     "organization_template" if scope == "GLOBAL" else "formal_document",
                     500, "[]", "[]"),
                )
            conn.execute(
                """INSERT INTO scenario_templates(template_id,project_id,name,
                scenario_category,template_json,version,status,chunk_id,created_at,updated_at)
                VALUES(?,?,?,?,?,1,'approved',?,?,?)""",
                (f"TPL-{suffix}", scope, f"飞控模板{suffix}", "飞控", "{}",
                 f"CHK-{suffix}", timestamp, timestamp),
            )
        conn.execute(
            """INSERT INTO scenario_cards(scenario_id,project_id,title,created_at,card_json)
            VALUES('SCN-LOCAL',?,?,?,'{}')""", (project_id, "飞控场景", timestamp))
        conn.execute(
            """INSERT INTO equipment_entities(
            equipment_id,project_id,name,created_at,updated_at)
            VALUES('EQ-LOCAL',?,'飞控设备LOCAL',?,?)""",
            (project_id, timestamp, timestamp),
        )
        conn.execute(
            """INSERT INTO scenario_equipment_allocations(
            allocation_id,project_id,scenario_id,role_requirement_id,equipment_id,quantity,
            configuration_json,capability_ids_json,rule_ids_json,assumptions_json,
            need_human_confirm,created_at,updated_at,source_refs_json)
            VALUES('ALLOC-LOCAL',?,'SCN-LOCAL','ROLE-1','EQ-LOCAL',2,'{}','[]',
            '["RULE-LOCAL"]','[]',0,?,?,?)""",
            (project_id, timestamp, timestamp, dumps_json([{"chunk_id": "CHK-LOCAL"}])),
        )
        conn.execute(
            """INSERT INTO scenario_validation_results(
            validation_id,project_id,scenario_id,is_valid,errors_json,warnings_json,
            missing_roles_json,conflicting_rule_ids_json,checked_allocation_ids_json,
            metrics_json,need_human_confirm,created_at,passed,score,
            blocking_issues_json,missing_information_json,check_results_json)
            VALUES('VAL-LOCAL',?,'SCN-LOCAL',1,'[]','[]','[]','[]','[]','{}',0,?,1,100,'[]','[]','{}')""",
            (project_id, timestamp),
        )
        conn.execute(
            """INSERT INTO approved_learning_rules(
            learning_rule_id,project_id,name,rule_type,condition_json,action_json,
            enabled,chunk_id,approved_by,approved_at,created_at)
            VALUES('RULE-FEEDBACK',?,'审核规则','test_rule','{}','{}',1,
            'CHK-LOCAL','reviewer',?,?)""", (project_id, timestamp, timestamp))
    for scope, suffix in ((project_id, "LOCAL"), (other_id, "OTHER"), ("GLOBAL", "GLOBAL")):
        manager.equipment.upsert_equipment(scope, {
            "project_id": scope, "equipment_id": f"EQ-{suffix}", "name": f"飞控设备{suffix}",
            "roles": ["飞控角色"], "capabilities": [], "aliases": [], "interfaces": [],
            "constraints": [], "simulation_parameters": {}, "raw_payload_json": "{}",
            "source_file": f"{suffix}.jsonl", "source_line_no": 1,
        })
    context = AgentRuntimeContext(
        project_id=project_id, manager=manager, settings=Settings(enable_ollama=False)
    )
    return context, other_id


def test_read_only_tools_track_sources_and_enforce_project_scope(grounded_context) -> None:
    context, other_id = grounded_context
    tools = {item.name: item for item in build_test_case_tools(context)}
    assert all("project_id" not in item.args for item in tools.values())
    local = tools["search_approved_knowledge"].invoke({"query": "飞控"})
    assert local and {row["project_id"] for row in local} == {context.project_id}
    merged = tools["search_approved_knowledge"].invoke({"query": "飞控", "allow_global": True})
    assert {row["project_id"] for row in merged} == {context.project_id, "GLOBAL"}
    assert other_id not in {row["project_id"] for row in merged}
    assert tools["get_simulation_model"].invoke({"query": "飞控"})
    assert tools["get_state_transitions"].invoke({"query": "飞控"})
    assert tools["get_verified_parameters"].invoke({"query": "飞控"})
    templates = tools["search_scenario_templates"].invoke({"query": "飞控", "allow_global": True})
    assert {row["project_id"] for row in templates} == {context.project_id, "GLOBAL"}
    candidates = tools["search_equipment_candidates"].invoke({"role": "飞控角色", "allow_global": True})
    assert {row["equipment_id"] for row in candidates["matches"]} == {"EQ-LOCAL", "EQ-GLOBAL"}
    allocations = tools["get_scenario_equipment_allocation"].invoke({"scenario_id": "SCN-LOCAL"})
    validation = tools["get_scenario_validation_result"].invoke({"scenario_id": "SCN-LOCAL"})
    feedback = tools["get_approved_feedback_rules"].invoke({})
    assert allocations[0]["configuration_rule_ids"] == ["RULE-LOCAL"]
    assert validation["scenario_validation_run_id"] == "VAL-LOCAL"
    assert feedback[0]["configuration_rule_id"] == "RULE-FEEDBACK"
    assert "EQ-LOCAL" in context.retrieved_equipment_ids
    assert {"RULE-LOCAL", "RULE-FEEDBACK"} <= set(context.retrieved_configuration_rule_ids)
    assert "VAL-LOCAL" in context.retrieved_scenario_validation_run_ids
    assert "CHK-LOCAL" in context.retrieved_source_chunk_ids


def test_final_provenance_uses_only_observed_tool_results(grounded_context) -> None:
    context, _ = grounded_context
    context.record_tool("search_approved_knowledge")
    context.record_chunks(["CHK-LOCAL"])
    context.record_knowledge(["K-parameter-LOCAL"])
    context.record_equipment(["EQ-LOCAL"])
    context.record_rules(["RULE-LOCAL"])
    context.record_validation_runs(["VAL-LOCAL"])
    context.record_scenarios(["SCN-LOCAL"])
    bundle = GeneratedCaseBundle(
        cases=[TestCase(
            case_id="TC-1", title="场景测试", objective="验证场景",
            test_steps=["执行"], expected_results=["结果"], evaluation_criteria="符合要求",
            requirement_ids=["FAKE-REQ"], source_chunk_ids=["FAKE-CHUNK"],
            scenario_ids=["FAKE-SCENARIO"],
        )],
        knowledge_unit_ids=["K-parameter-LOCAL", "FAKE-K"],
        equipment_ids=["EQ-LOCAL", "FAKE-EQ"],
        configuration_rule_ids=["RULE-LOCAL", "FAKE-RULE"],
        scenario_validation_run_id="FAKE-VAL",
    )
    agent = TestCaseAgent(context, BindableModel(responses=[]))
    cleaned = agent._apply_observed_provenance(
        bundle, TestCaseAgentRequest(requirement_ids=["REQ-1"])
    )
    assert cleaned.knowledge_unit_ids == ["K-parameter-LOCAL"]
    assert cleaned.equipment_ids == ["EQ-LOCAL"]
    assert cleaned.configuration_rule_ids == ["RULE-LOCAL"]
    assert cleaned.scenario_validation_run_id == "VAL-LOCAL"
    assert cleaned.cases[0].source_chunk_ids == ["CHK-LOCAL"]
    assert cleaned.cases[0].requirement_ids == ["REQ-1"]
    assert cleaned.cases[0].scenario_ids == ["SCN-LOCAL"]
