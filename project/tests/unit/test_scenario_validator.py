from pathlib import Path

import pytest

import core.project_manager as manager_module
from core.project_manager import ProjectManager
from domain.schemas.allocation import EquipmentAllocation
from domain.schemas.scenario_spec import ScenarioRoleRequirement, ScenarioSpec
from infrastructure.db.json_codec import dumps_json
from infrastructure.db.repositories.base import now_iso
from scenario_engine.scenario_validator import CHECK_NAMES, ScenarioValidator


@pytest.fixture
def validator_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    manager = ProjectManager(tmp_path / "validator.db")
    project_id = manager.create_project("validator project")["project_id"]
    other_id = manager.create_project("other project")["project_id"]
    chunks = {}
    for scope in (project_id, other_id):
        path = manager.uploads_dir(scope) / "source.txt"
        path.write_text("状态和参数资料", encoding="utf-8")
        document_id = manager.add_document(scope, path.name, "txt", path, f"hash-{scope}", "txt")
        chunks[scope] = manager.replace_chunks(scope, document_id, [{"chunk_text": "状态和参数资料"}])[0]
    manager.upsert_requirement(project_id, {
        "requirement_id": "REQ-1", "title": "状态验证", "description": "验证状态变化",
        "source_chunk_id": chunks[project_id],
    })
    for scope, equipment_id in ((project_id, "EQ-LOCAL"), (other_id, "EQ-FOREIGN")):
        manager.equipment.upsert_equipment(scope, {
            "project_id": scope, "equipment_id": equipment_id, "name": equipment_id,
            "roles": ["执行角色"], "capabilities": [{"name": "状态采集"}],
            "aliases": [], "interfaces": [], "constraints": [],
            "simulation_parameters": {}, "raw_payload_json": "{}",
        })
    timestamp = now_iso()
    with manager.connections.transaction() as conn:
        for unit_id, source_kind, value in (("K-PROJECT", "formal_document", 10), ("K-BOOK", "book", 20)):
            conn.execute(
                """INSERT INTO knowledge_units(
                knowledge_unit_id,project_id,knowledge_type,title,content,
                normalized_data_json,tags_json,document_id,chunk_id,confidence,
                need_human_confirm,status,created_at,updated_at,source_kind,priority
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (unit_id, project_id, "parameter", "速度", f"速度={value}",
                 dumps_json({"name": "速度", "value": value, "unit": "m/s"}), "[]",
                 None, chunks[project_id], 1.0, 0, "approved", timestamp, timestamp,
                 source_kind, 500 if source_kind == "formal_document" else 200),
            )
    return manager, project_id, other_id, chunks


def make_scenario(project_id: str, chunk_id: str, **updates) -> ScenarioSpec:
    role = ScenarioRoleRequirement(
        role_requirement_id="ROLE-1", role_name="执行角色",
        description="执行状态采集功能", required_capabilities=["状态采集"],
    )
    allocation = EquipmentAllocation(
        allocation_id="ALLOC-1", project_id=project_id, scenario_id="SCN-1",
        role_requirement_id="ROLE-1", equipment_id="EQ-LOCAL", quantity=2,
        rule_ids=["RULE-1"],
    )
    data = dict(
        scenario_id="SCN-1", project_id=project_id, title="状态场景",
        scenario_goal="验证状态变化", simulation_object="系统",
        role_requirements=[role], actors=[role.role_name], equipment_allocations=[allocation],
        initial_state={"模式": "待机"}, trigger_events=["收到启动指令"],
        normal_flow=["执行状态采集"], observed_variables={
            "速度": {"value": 10, "unit": "m/s", "operating_condition": "正常模式"}
        }, success_criteria=["观察 速度：达到项目要求"],
        requirement_ids=["REQ-1"], source_chunk_ids=[chunk_id],
    )
    data.update(updates)
    return ScenarioSpec(**data)


def valid_provenance(chunk_id: str) -> dict:
    return {
        "field_sources": {
            "initial_state": {"chunk_ids": [chunk_id]},
            "normal_flow": {"chunk_ids": [chunk_id]},
            "observed_variables.速度": {"chunk_id": chunk_id},
        },
        "allow_global": False,
    }


def test_complete_scenario_passes_all_checks(validator_data) -> None:
    manager, project_id, _, chunks = validator_data
    result = ScenarioValidator(manager).validate(
        make_scenario(project_id, chunks[project_id]), valid_provenance(chunks[project_id])
    )
    assert result.passed and result.is_valid
    assert result.score == 100
    assert set(result.check_results) == set(CHECK_NAMES)
    assert not result.blocking_issues


def test_all_required_blocking_rules_are_enforced(validator_data) -> None:
    manager, project_id, other_id, chunks = validator_data
    scenario = make_scenario(
        project_id, "CHUNK-NOT-FOUND",
        source_chunk_ids=["CHUNK-NOT-FOUND", chunks[other_id]],
        knowledge_unit_ids=["K-BOOK"],
        equipment_allocations=[EquipmentAllocation(
            allocation_id="A-X", project_id=project_id, scenario_id="SCN-1",
            role_requirement_id="ROLE-1", equipment_id="EQ-FOREIGN", quantity=3,
        )],
        success_criteria=["结果正确"],
    )
    result = ScenarioValidator(manager).validate(scenario, valid_provenance(chunks[project_id]))
    assert not result.passed
    joined = "\n".join(result.blocking_issues)
    assert "其他项目" in joined
    assert "装备不存在" in joined
    assert "没有rule_id或用户来源" in joined
    assert "通用书籍参数" in joined
    assert "不存在的chunk" in joined
    assert "无法对应可观测变量" in joined
    assert result.need_human_confirm


def test_non_blocking_gaps_allow_draft_but_require_confirmation(validator_data) -> None:
    manager, project_id, _, chunks = validator_data
    scenario = make_scenario(
        project_id, chunks[project_id],
        role_requirements=[ScenarioRoleRequirement(
            role_requirement_id="ROLE-MISSING", role_name="监视角色",
            description="监视状态", required_capabilities=["未知能力"],
        )],
        equipment_allocations=[],
        initial_state={
            "模式": {"before": "待机", "after": "待机", "transition_required": True},
            "速度": 10,
        }, trigger_events=["异常触发"], normal_flow=["执行异常监视"],
        abnormal_flows=[["发生异常"]], recovery_flow=[],
    )
    result = ScenarioValidator(manager).validate(scenario, valid_provenance(chunks[project_id]))
    assert result.passed
    assert result.score < 100
    assert result.warnings
    assert result.missing_information
    assert result.need_human_confirm
    assert not result.check_results["role_completeness"]["passed"]
    assert not result.check_results["parameter_traceability"]["passed"]
    assert not result.check_results["recovery_completeness"]["passed"]
