from pathlib import Path

import pytest

import application.services.project_service as manager_module
from application.services.project_service import ProjectManager
from domain.rules.equipment_allocation import AllocationRule
from workflows.scenario.equipment_allocator import EquipmentAllocator


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectManager:
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    return ProjectManager(tmp_path / "workspace.db")


def _save_equipment(manager: ProjectManager, project_id: str, equipment_id: str) -> None:
    manager.equipment.upsert_equipment(
        project_id,
        {
            "equipment_id": equipment_id,
            "project_id": project_id,
            "name": "分配设备",
            "aliases": [],
            "category": "设备",
            "platform_type": "平台",
            "roles": [],
            "capabilities": [],
            "interfaces": [],
            "constraints": [],
            "simulation_parameters": {},
            "source_file": "equipment.jsonl",
            "source_line_no": 1,
            "raw_payload_json": "{}",
            "record_hash": f"hash-{project_id}-{equipment_id}",
        },
    )


def test_allocator_uses_current_project_inventory_without_silent_reduction(
    manager: ProjectManager,
) -> None:
    project_id = manager.create_project("分配项目")["project_id"]
    _save_equipment(manager, project_id, "EQ-1")
    manager.equipment.upsert_inventory(project_id, "EQ-1", 2)
    rules = [
        AllocationRule(
            rule_id="R-FIX",
            role="节点",
            equipment_id="EQ-1",
            rule_type="fixed",
            parameters={"quantity": 3},
        ),
        AllocationRule(
            rule_id="R-INV",
            role="节点",
            equipment_id="EQ-1",
            rule_type="inventory_limit",
            parameters={},
        ),
    ]
    result = EquipmentAllocator(manager.equipment).allocate(project_id, rules)[0]
    assert result.quantity == 3
    assert "库存不足:需求3,可用2" in result.conflicts


def test_allocator_rejects_cross_project_equipment(manager: ProjectManager) -> None:
    first = manager.create_project("项目一")["project_id"]
    second = manager.create_project("项目二")["project_id"]
    _save_equipment(manager, first, "EQ-2")
    rule = AllocationRule(
        rule_id="R",
        role="节点",
        equipment_id="EQ-2",
        rule_type="fixed",
        parameters={"quantity": 1},
    )
    with pytest.raises(ValueError, match="outside the explicit project scope"):
        EquipmentAllocator(manager.equipment).allocate(second, [rule])

