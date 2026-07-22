from __future__ import annotations

from pathlib import Path

import pytest

import application.services.project_service as manager_module
from application.services.project_service import ProjectManager
from infrastructure.equipment.jsonl_importer import MilitaryJsonlImporter
from infrastructure.equipment.normalizer import EquipmentFieldMapping


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectManager:
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    return ProjectManager(tmp_path / "workspace.db")


def _write(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_stream_import_handles_normal_blank_invalid_duplicate_and_continues(
    manager: ProjectManager, tmp_path: Path
) -> None:
    project_id = manager.create_project("装备项目")["project_id"]
    path = _write(
        tmp_path / "equipment.jsonl",
        [
            '{"name":"歼-16战机","大类":"飞行器","类型":"战斗机","最大航程":"4288千米","content":"说明"}',
            "",
            "{bad json",
            '{"name":"歼-16战机","大类":"飞行器","类型":"战斗机","最大航程":"4288千米","content":"说明"}',
            '{"name":"后续设备","大类":"飞行器","类型":"运输机","content":"继续导入"}',
        ],
    )
    report = MilitaryJsonlImporter(manager.equipment).import_file(path, project_id)
    assert report.total_lines == 5
    assert report.success_count == 2
    assert report.skipped_count == 1
    assert report.duplicate_count == 1
    assert report.failure_count == 1
    assert report.field_coverage["name"] == 3
    assert report.errors[0].error_type == "invalid_json"
    assert manager.equipment.list_import_errors(project_id)[0]["source_line_no"] == 3
    result = manager.equipment.search_by_capability(project_id, "最大航程")
    assert result[0]["name"] == "歼-16战机"


def test_chinese_alias_unknown_field_and_raw_provenance_are_preserved(
    manager: ProjectManager, tmp_path: Path
) -> None:
    project_id = manager.create_project("中文别名")["project_id"]
    path = _write(
        tmp_path / "aliases.jsonl",
        [
            '{"name":"FC-1／枭龙","别名":"雷电，JF-17","大类":"飞行器",'
            '"类型":"战斗机","未知属性":"原样保留","content":"装备说明"}'
        ],
    )
    mapping = EquipmentFieldMapping(alias_fields=["别名"])
    report = MilitaryJsonlImporter(manager.equipment, mapping).import_file(
        path, project_id
    )
    assert report.success_count == 1
    by_alias = manager.equipment.search_by_name_or_alias(project_id, "枭龙")
    assert by_alias[0]["name"] == "FC-1／枭龙"
    assert {"雷电", "JF-17", "FC-1", "枭龙"} <= set(by_alias[0]["aliases"])
    assert by_alias[0]["simulation_parameters"]["未知属性"] == "原样保留"
    assert by_alias[0]["raw_payload"]["未知属性"] == "原样保留"
    assert by_alias[0]["source_file"] == str(path)
    assert by_alias[0]["source_line_no"] == 1
    assert len(by_alias[0]["record_hash"]) == 64


def test_missing_name_is_skipped_and_reported(
    manager: ProjectManager, tmp_path: Path
) -> None:
    project_id = manager.create_project("缺名记录")["project_id"]
    path = _write(tmp_path / "missing.jsonl", ['{"大类":"舰船舰艇"}'])
    report = MilitaryJsonlImporter(manager.equipment).import_file(path, project_id)
    assert report.missing_name_count == 1
    assert report.skipped_count == 1
    assert report.success_count == 0
    assert report.errors[0].error_type == "missing_name"


def test_reimport_is_idempotent(manager: ProjectManager, tmp_path: Path) -> None:
    project_id = manager.create_project("幂等导入")["project_id"]
    path = _write(
        tmp_path / "stable.jsonl",
        ['{"name":"稳定设备","大类":"火炮","类型":"自行火炮","射程":"20千米"}'],
    )
    importer = MilitaryJsonlImporter(manager.equipment)
    first = importer.import_file(path, project_id)
    reordered = _write(
        tmp_path / "stable-reordered.jsonl",
        ['{"射程":"20千米","类型":"自行火炮","大类":"火炮","name":"稳定设备"}'],
    )
    reordered_report = importer.import_file(reordered, project_id)
    second = importer.import_file(path, project_id)
    assert first.success_count == 1
    assert reordered_report.duplicate_count == 1
    assert second.success_count == 0
    assert second.duplicate_count == 1
    assert len(manager.equipment.list_by_category(project_id, "火炮")) == 1


def test_project_and_global_scope_require_explicit_opt_in(
    manager: ProjectManager, tmp_path: Path
) -> None:
    project_id = manager.create_project("本地项目")["project_id"]
    manager.equipment.ensure_global_project()
    global_path = _write(
        tmp_path / "global.jsonl",
        ['{"name":"全局雷达","大类":"太空装备","类型":"雷达","content":"全局事实"}'],
    )
    local_path = _write(
        tmp_path / "local.jsonl",
        ['{"name":"本地终端","大类":"太空装备","类型":"终端","content":"本地事实"}'],
    )
    importer = MilitaryJsonlImporter(manager.equipment)
    importer.import_file(global_path, "GLOBAL")
    importer.import_file(local_path, project_id)
    assert manager.equipment.search_by_name_or_alias(project_id, "全局雷达") == []
    global_result = manager.equipment.search_by_name_or_alias(
        project_id, "全局雷达", allow_global=True
    )
    assert global_result[0]["project_id"] == "GLOBAL"
    assert manager.equipment.search_by_name_or_alias(
        "GLOBAL", "本地终端", allow_global=True
    ) == []
    assert len(manager.equipment.list_by_category(project_id, "太空装备")) == 1
    assert len(
        manager.equipment.list_by_category(
            project_id, "太空装备", allow_global=True
        )
    ) == 2


def test_exact_alias_match_precedes_partial_name_match(
    manager: ProjectManager, tmp_path: Path
) -> None:
    project_id = manager.create_project("匹配优先级")["project_id"]
    path = _write(
        tmp_path / "priority.jsonl",
        [
            '{"name":"雷达模拟设备","别名":"辅助设备","大类":"设备"}',
            '{"name":"远程传感器","别名":"雷达","大类":"设备"}',
        ],
    )
    MilitaryJsonlImporter(
        manager.equipment, EquipmentFieldMapping(alias_fields=["别名"])
    ).import_file(path, project_id)
    results = manager.equipment.search_by_name_or_alias(project_id, "雷达")
    assert results[0]["name"] == "远程传感器"
