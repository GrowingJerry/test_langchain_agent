"""Validation tests for scenario compilation, equipment, and learning schemas."""

import pytest
from pydantic import ValidationError

from domain.schemas import (
    DocumentSection,
    EquipmentAllocation,
    EquipmentCapability,
    EquipmentConfigurationRule,
    EquipmentEntity,
    EquipmentRoleMapping,
    KnowledgeConflict,
    KnowledgeReview,
    KnowledgeUnit,
    LearningTask,
    ScenarioIntent,
    ScenarioRoleRequirement,
    ScenarioSpec,
    ScenarioValidationResult,
)


def test_equipment_models_validate_and_forbid_extra_fields() -> None:
    capability = EquipmentCapability(
        capability_id="CAP-1", equipment_id="EQ-1", name="通信"
    )
    entity = EquipmentEntity(
        equipment_id="EQ-1",
        project_id="P-1",
        name="设备",
        capabilities=[capability],
        jsonl_record_no=42,
    )
    assert entity.capabilities[0].capability_id == "CAP-1"
    assert EquipmentRoleMapping(
        mapping_id="MAP-1", project_id="P-1", equipment_id="EQ-1", role_name="通信节点"
    ).capability_ids == []
    assert EquipmentConfigurationRule(
        rule_id="RULE-1", project_id="P-1", name="配置规则"
    ).enabled
    with pytest.raises(ValidationError):
        EquipmentEntity(
            equipment_id="EQ-2", project_id="P-1", name="设备", unknown=True
        )


def test_learning_and_knowledge_list_defaults_are_not_shared() -> None:
    first = LearningTask(task_id="L-1", project_id="P-1", task_type="full")
    second = LearningTask(task_id="L-2", project_id="P-1", task_type="full")
    first.document_ids.append("DOC-1")
    assert second.document_ids == []
    section = DocumentSection(
        section_id="SEC-1", project_id="P-1", document_id="DOC-1"
    )
    assert section.chunk_ids == []
    unit = KnowledgeUnit(
        knowledge_unit_id="KU-1",
        project_id="P-1",
        knowledge_type="requirement",
        content="事实",
    )
    assert unit.normalized_data == {}
    assert KnowledgeConflict(
        conflict_id="KCF-1", project_id="P-1", conflict_type="value"
    ).knowledge_unit_ids == []
    assert KnowledgeReview(
        review_id="KRV-1",
        project_id="P-1",
        knowledge_unit_id="KU-1",
        status="approved",
    ).comments == []


def test_scenario_spec_contains_compilation_inputs_and_allocations() -> None:
    role = ScenarioRoleRequirement(
        role_requirement_id="ROLE-1", role_name="目标设备", required_capabilities=["通信"]
    )
    allocation = EquipmentAllocation(
        allocation_id="ALLOC-1",
        project_id="P-1",
        scenario_id="SCN-1",
        role_requirement_id="ROLE-1",
        equipment_id="EQ-1",
    )
    spec = ScenarioSpec(
        scenario_id="SCN-1",
        project_id="P-1",
        title="通信异常恢复",
        scenario_goal="验证异常后的恢复能力",
        role_requirements=[role],
        equipment_allocations=[allocation],
        confidence=0.8,
    )
    assert spec.role_requirements[0].role_name == "目标设备"
    assert spec.equipment_allocations[0].quantity == 1
    assert ScenarioIntent(
        intent_id="INT-1", project_id="P-1", title="恢复", goal="验证恢复"
    ).requirement_ids == []
    assert ScenarioValidationResult(
        validation_id="VAL-1", project_id="P-1", scenario_id="SCN-1", is_valid=True
    ).errors == []
    with pytest.raises(ValidationError):
        ScenarioSpec(
            scenario_id="SCN-X",
            project_id="P-1",
            title="无效",
            scenario_goal="无效置信度",
            confidence=1.1,
        )

