from pathlib import Path

import pytest

import application.services.project_service as manager_module
from application.services.project_service import ProjectManager
from infrastructure.database.json_codec import dumps_json
from infrastructure.repositories.base import now_iso
from workflows.scenario.variant_expansion import expand_scenario
from workflows.scenario.scenario_workflow import ScenarioWorkflow


@pytest.fixture
def scenario_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    manager = ProjectManager(tmp_path / "scenario.db")
    project_id = manager.create_project("飞控仿真项目")["project_id"]
    source = manager.uploads_dir(project_id) / "requirements.txt"
    source.write_text("飞控系统应监视高度并在故障后进入安全状态", encoding="utf-8")
    document_id = manager.add_document(project_id, source.name, "txt", source, "scenario-doc", "txt")
    chunk_id = manager.replace_chunks(project_id, document_id, [{
        "chunk_text": "飞控系统应监视高度状态变量，并在通信故障后执行安全恢复",
        "page_no": 8, "page_start": 8, "page_end": 8,
    }])[0]
    manager.upsert_requirement(project_id, {
        "requirement_id": "REQ-FC-1", "title": "飞控故障恢复",
        "description": "飞控系统应监视高度并在通信故障后恢复",
        "source_document": source.name, "source_chunk_id": chunk_id,
    })
    manager.save_profile(project_id, {
        "project_name": "飞控仿真项目", "domain": "飞行仿真",
        "test_object": "被测飞行器", "main_functions": ["飞控"],
    })
    timestamp = now_iso()
    with manager.connections.transaction() as conn:
        units = [
            ("K-STATE", "state_variable", "高度", "飞控高度是可观测状态变量",
             {"name": "高度", "symbol": "h", "unit": "m"}),
            ("K-FAULT", "fault_mode", "通信故障", "飞控通信故障需要安全响应",
             {"recovery_flow": ["系统响应故障并进入安全状态", "恢复通信后重新同步数据"]}),
            ("K-LIMIT", "constraint", "高度边界", "高度边界值应按项目正式资料执行", {}),
        ]
        for unit_id, kind, title, content, normalized in units:
            conn.execute(
                """INSERT INTO knowledge_units(
                knowledge_unit_id,project_id,knowledge_type,title,content,
                normalized_data_json,tags_json,document_id,chunk_id,page_no,
                confidence,need_human_confirm,status,created_at,updated_at,
                applicable_conditions_json,inapplicable_conditions_json,section_scope,
                source_kind,priority) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (unit_id, project_id, kind, title, content, dumps_json(normalized), "[]",
                 document_id, chunk_id, 8, 1.0, 0, "approved", timestamp, timestamp,
                 dumps_json(["飞控任务阶段"]), "[]", "飞控", "formal_document", 500),
            )
        conn.execute(
            """INSERT INTO scenario_templates(
            template_id,project_id,name,scenario_category,template_json,version,status,
            document_id,chunk_id,page_no,created_at,updated_at)
            VALUES(?,?,?,?,?,1,'approved',?,?,?,?,?)""",
            ("TPL-FC", project_id, "飞控故障场景模板", "飞控",
             dumps_json({
                 "tags": ["飞控", "故障"],
                 "role_requirements": [{
                     "role_name": "飞控执行器", "description": "执行飞控指令并反馈状态",
                     "required_capabilities": [], "min_quantity": 1, "max_quantity": 2,
                 }],
                 "normal_flow": ["下发项目资料规定的飞控指令", "采集高度状态"],
             }), document_id, chunk_id, 8, timestamp, timestamp),
        )
    manager.equipment.upsert_equipment(project_id, {
        "project_id": project_id, "equipment_id": "EQ-FC", "name": "飞控执行设备",
        "roles": ["飞控执行器"], "capabilities": [], "aliases": [],
        "interfaces": [], "constraints": [], "simulation_parameters": {},
        "source_description": "项目装备清单", "source_file": source.name,
        "source_line_no": 1, "raw_payload_json": "{}",
    })
    with manager.connections.transaction() as conn:
        conn.execute(
            """INSERT INTO equipment_configuration_rules(
            rule_id,project_id,name,description,condition_expression,required_roles_json,
            required_capabilities_json,compatible_equipment_types_json,
            incompatible_equipment_ids_json,parameters_json,enabled,document_id,chunk_id,
            page_no,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)""",
            ("RULE-FIXED-2", project_id, "双机配置", "项目固定双机配置", "fixed",
             dumps_json(["飞控执行器"]), "[]", "[]", "[]",
             dumps_json({"rule_type": "fixed", "equipment_id": "EQ-FC",
                         "parameters": {"quantity": 2, "unit": "entity"}}),
             document_id, chunk_id, 8, timestamp, timestamp),
        )
    return manager, project_id, chunk_id


def test_non_ollama_scenario_compilation_end_to_end(scenario_project) -> None:
    manager, project_id, chunk_id = scenario_project
    result = ScenarioWorkflow(manager).run(project_id, {
        "scenario_goal": "验证飞控通信故障和高度边界响应",
        "simulation_object": "被测飞行器", "target_subsystem": "飞控",
        "mission_phase": "任务阶段", "scale": 2,
        "focus_risks": ["故障", "边界", "恢复"],
        "use_project_defaults": False, "additional_instructions": "仅使用项目事实",
    })
    categories = [item.scenario_category for item in result.scenarios]
    assert categories == ["nominal", "boundary", "abnormal", "recovery"]
    assert "degraded" not in categories and "concurrency" not in categories
    main = result.scenarios[0]
    assert main.source_chunk_ids == [chunk_id]
    assert set(main.knowledge_unit_ids) == {"K-STATE", "K-FAULT", "K-LIMIT"}
    assert main.role_requirements[0].description
    assert main.equipment_allocations[0].equipment_id == "EQ-FC"
    assert main.equipment_allocations[0].quantity == 2
    assert main.equipment_allocations[0].rule_ids == ["RULE-FIXED-2"]
    assert main.observed_variables["高度"]["unit"] == "m"
    assert all("观察" in item for item in main.success_criteria)
    assert all(validation.is_valid for validation in result.validations)
    assert result.provenance["template_id"] == "TPL-FC"
    with manager.connections.connection() as conn:
        run = conn.execute("SELECT * FROM scenario_generation_runs WHERE run_id=?", (result.run_id,)).fetchone()
        assert run is not None and run["provenance_json"]
        assert conn.execute("SELECT COUNT(*) FROM scenario_cards WHERE project_id=?", (project_id,)).fetchone()[0] == 4

    drafts = manager.scenarios.list_compiled(project_id, "draft")
    assert len(drafts) == 4
    reviewed = manager.scenarios.review_compiled(
        project_id,
        main.scenario_id,
        accept_non_blocking=True,
        blocking_resolutions={},
        approve=True,
        reviewer="integration-reviewer",
    )
    assert reviewed["compilation_status"] == "approved"
    approved = manager.scenarios.list_compiled(project_id, "approved")
    assert [item["scenario_id"] for item in approved] == [main.scenario_id]


def test_legacy_expand_scenario_calls_workflow(scenario_project, monkeypatch: pytest.MonkeyPatch) -> None:
    manager, project_id, _ = scenario_project
    monkeypatch.setattr(manager_module, "ProjectManager", lambda: manager)
    rows = expand_scenario({
        "project_id": project_id, "scenario_goal": "验证飞控通信故障",
        "simulation_object": "被测飞行器", "target_subsystem": "飞控",
        "focus_risks": "故障", "use_project_defaults": "false",
    })
    assert rows
    assert {"scenario_id", "scenario_name", "scenario_type", "actions"} <= rows[0].keys()
