from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import pytest

import application.services.project_service as manager_module
from application.services.project_service import ProjectManager
from infrastructure.equipment.equipment_service import EquipmentService
from infrastructure.retrieval.equipment_retriever import EquipmentRetriever


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProjectManager:
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    return ProjectManager(tmp_path / "workspace.db")


def _equipment(
    project_id: str,
    equipment_id: str,
    name: str,
    *,
    aliases: Optional[List[str]] = None,
    category: str = "飞行器",
    roles: Optional[List[str]] = None,
    capabilities: Optional[List[str]] = None,
    interfaces: Optional[List[str]] = None,
    constraints: Optional[List[str]] = None,
) -> dict:
    return {
        "equipment_id": equipment_id,
        "project_id": project_id,
        "name": name,
        "aliases": aliases or [],
        "category": category,
        "platform_type": "测试平台",
        "roles": roles or [],
        "capabilities": [
            {"name": capability, "description": capability}
            for capability in capabilities or []
        ],
        "interfaces": interfaces or [],
        "constraints": constraints or [],
        "simulation_parameters": {},
        "source_description": f"{name}来源说明",
        "source_file": "military.jsonl",
        "source_line_no": int(equipment_id.rsplit("-", 1)[-1]),
        "raw_payload_json": "{}",
        "record_hash": f"hash-{project_id}-{equipment_id}",
    }


def test_exact_name_precedes_other_candidates(manager: ProjectManager) -> None:
    project_id = manager.create_project("名称优先")["project_id"]
    manager.equipment.upsert_equipment(
        project_id, _equipment(project_id, "EQ-1", "雷达")
    )
    manager.equipment.upsert_equipment(
        project_id, _equipment(project_id, "EQ-2", "雷达模拟设备")
    )
    result = EquipmentService(manager.equipment).search(project_id, name="雷达")
    assert result.matches[0].equipment_id == "EQ-1"
    assert result.matches[0].score == 100
    assert result.matches[0].match_reason.startswith("精确名称匹配")
    assert result.matches[0].source_refs[0]["source_line_no"] == 1


def test_exact_chinese_alias_match(manager: ProjectManager) -> None:
    project_id = manager.create_project("别名匹配")["project_id"]
    manager.equipment.upsert_equipment(
        project_id,
        _equipment(project_id, "EQ-3", "FC-1战斗机", aliases=["枭龙", "雷电"]),
    )
    result = EquipmentService(manager.equipment).find_exact(project_id, "枭龙")
    assert result.matches[0].equipment_id == "EQ-3"
    assert "精确别名匹配" in result.matches[0].match_reason


def test_role_capability_and_interface_structured_matching(
    manager: ProjectManager,
) -> None:
    project_id = manager.create_project("结构化匹配")["project_id"]
    manager.equipment.upsert_equipment(
        project_id,
        _equipment(
            project_id,
            "EQ-4",
            "通信节点",
            category="通信装备",
            roles=["中继节点"],
            capabilities=["数据转发", "状态监测"],
            interfaces=["UDP", "以太网"],
        ),
    )
    service = EquipmentService(manager.equipment)
    result = service.search(
        project_id,
        category="通信装备",
        roles=["中继节点"],
        capabilities=["数据转发"],
        interfaces=["UDP"],
    )
    assert result.matches[0].matched_roles == ["中继节点"]
    assert result.matches[0].matched_capabilities == ["数据转发"]
    assert result.matches[0].violated_constraints == []


def test_missing_capability_returns_diagnostic_not_applicable_match(
    manager: ProjectManager,
) -> None:
    project_id = manager.create_project("能力缺失")["project_id"]
    manager.equipment.upsert_equipment(
        project_id,
        _equipment(project_id, "EQ-5", "观测设备", capabilities=["图像采集"]),
    )
    result = EquipmentService(manager.equipment).find_by_capabilities(
        project_id, ["图像采集", "红外测距"]
    )
    assert result.matches == []
    assert result.rejected_matches[0].missing_capabilities == ["红外测距"]
    assert result.rejected_matches[0].need_human_confirm is True
    assert "缺少能力：红外测距" in result.missing_information


def test_constraint_conflict_excludes_candidate(manager: ProjectManager) -> None:
    project_id = manager.create_project("约束冲突")["project_id"]
    manager.equipment.upsert_equipment(
        project_id,
        _equipment(
            project_id,
            "EQ-6",
            "室内终端",
            capabilities=["数据转发"],
            constraints=["禁止低温环境"],
        ),
    )
    result = EquipmentService(manager.equipment).search(
        project_id,
        capabilities=["数据转发"],
        prohibited_constraints=["禁止低温环境"],
    )
    assert result.matches == []
    assert "约束冲突:禁止低温环境" in result.rejected_matches[0].violated_constraints
    assert result.rejected_matches[0].score == 0


def test_current_project_inventory_shortage_is_reported(
    manager: ProjectManager,
) -> None:
    project_id = manager.create_project("库存过滤")["project_id"]
    manager.equipment.upsert_equipment(
        project_id, _equipment(project_id, "EQ-7", "库存设备")
    )
    manager.equipment.upsert_inventory(project_id, "EQ-7", 1)
    result = EquipmentService(manager.equipment).search(
        project_id, name="库存设备", inventory_only=True, required_quantity=2
    )
    assert result.matches == []
    assert "库存不足" in result.rejected_matches[0].violated_constraints[0]
    assert "当前项目库存中没有满足数量要求的装备" in result.missing_information


def test_current_project_shadows_same_named_global_equipment(
    manager: ProjectManager,
) -> None:
    project_id = manager.create_project("合并优先级")["project_id"]
    manager.equipment.ensure_global_project()
    manager.equipment.upsert_equipment(
        "GLOBAL", _equipment("GLOBAL", "EQ-8", "通用雷达", capabilities=["全局能力"])
    )
    manager.equipment.upsert_equipment(
        project_id,
        _equipment(project_id, "EQ-9", "通用雷达", capabilities=["项目能力"]),
    )
    result = EquipmentService(manager.equipment).search(
        project_id, name="通用雷达", allow_global=True
    )
    assert [item.equipment_id for item in result.matches] == ["EQ-9"]
    assert result.matches[0].source_refs[0]["project_id"] == project_id


def test_global_equipment_inventory_requires_explicit_scope(manager: ProjectManager) -> None:
    project_id = manager.create_project("GLOBAL库存隔离")["project_id"]
    manager.equipment.ensure_global_project()
    manager.equipment.upsert_equipment(
        "GLOBAL", _equipment("GLOBAL", "EQ-100", "组织装备")
    )
    with pytest.raises(ValueError, match="allowed scope"):
        manager.equipment.upsert_inventory(project_id, "EQ-100", 1)
    manager.equipment.upsert_inventory(
        project_id, "EQ-100", 1, allow_global=True
    )
    assert manager.equipment.inventory_quantity(project_id, "EQ-100") == 1


def test_no_match_returns_missing_information_without_fabrication(
    manager: ProjectManager,
) -> None:
    project_id = manager.create_project("无匹配")["project_id"]
    result = EquipmentService(manager.equipment).find_exact(project_id, "不存在装备")
    assert result.matches == []
    assert result.rejected_matches == []
    assert result.missing_information
    assert all(item.equipment_id != "不存在装备" for item in result.matches)


class ConstantEmbeddingProvider:
    model = "fake"

    def embed(self, text: str) -> Optional[List[float]]:
        return [1.0, 0.0]


def test_embedding_cannot_override_structured_constraint_failure(
    manager: ProjectManager,
) -> None:
    project_id = manager.create_project("语义不得越权")["project_id"]
    manager.equipment.upsert_equipment(
        project_id,
        _equipment(
            project_id,
            "EQ-10",
            "高相似设备",
            constraints=["禁止仿真环境"],
        ),
    )
    retriever = EquipmentRetriever(
        manager.equipment, embedding_provider=ConstantEmbeddingProvider()
    )
    result = EquipmentService(manager.equipment, retriever=retriever).search(
        project_id,
        description="高相似设备",
        prohibited_constraints=["禁止仿真环境"],
    )
    assert result.used_embedding is True
    assert result.matches == []
    assert result.rejected_matches[0].score == 0
    assert "结构化约束校验未通过" in result.rejected_matches[0].match_reason
