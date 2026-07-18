"""Unit tests for the canonical test-case domain schema."""

import pytest
from pydantic import ValidationError

from domain.schemas.test_case import TestCase


def _canonical_data() -> dict:
    return {
        "case_id": "TC-001",
        "title": "订单提交成功",
        "objective": "验证有效订单可以被系统接收。",
        "preconditions": ["订单服务已启动"],
        "test_steps": ["提交有效订单"],
        "expected_results": ["系统返回订单编号"],
        "evaluation_criteria": "结果符合需求文档规定。",
        "test_data": ["有效订单数据"],
        "environment": ["测试环境"],
        "requirement_ids": ["REQ-001"],
        "scenario_ids": ["SCN-001"],
        "source_chunk_ids": ["CHK-001"],
        "source_documents": ["需求说明书.docx"],
        "quality_category": ["功能性"],
        "test_method": "等价类划分",
        "generation_mode": "rule_fallback",
    }


def test_canonical_fields_validate() -> None:
    case = TestCase(**_canonical_data())
    assert case.case_id == "TC-001"
    assert case.requirement_ids == ["REQ-001"]
    assert case.need_human_confirm is False


def test_legacy_fields_are_read_into_canonical_fields() -> None:
    legacy = {
        "case_id": "TC-LEGACY",
        "case_name": "旧用例名称",
        "test_purpose": "验证旧数据兼容读取。",
        "prerequisites": "系统已启动",
        "test_steps": "执行操作",
        "expected_result": "操作成功",
        "pass_criteria": "按需求文档规定值判定",
        "test_input": "输入 A",
        "test_environment": "环境 A",
        "requirement_id": "REQ-OLD",
        "scenario_id": "SCN-OLD",
        "source_document": "old.docx",
        "source_chunk_ids": ["CHK-OLD"],
        "six_quality_attribute": "可靠性",
        "need_human_confirmation": True,
        "generation_mode": "legacy_import",
    }
    case = TestCase.from_legacy_dict(legacy)
    assert case.title == "旧用例名称"
    assert case.expected_results == ["操作成功"]
    assert case.evaluation_criteria == "按需求文档规定值判定"
    assert case.source_documents == ["old.docx"]
    assert case.need_human_confirm is True


@pytest.mark.parametrize(
    "missing",
    [
        "case_id",
        "title",
        "objective",
        "test_steps",
        "expected_results",
        "evaluation_criteria",
    ],
)
def test_missing_required_field_has_clear_validation_error(missing: str) -> None:
    data = _canonical_data()
    data.pop(missing)
    with pytest.raises(ValidationError) as exc_info:
        TestCase(**data)
    assert missing in str(exc_info.value)


def test_missing_information_forces_human_confirmation() -> None:
    data = _canonical_data()
    data["need_human_confirm"] = False
    data["missing_information"] = ["性能阈值", "性能阈值"]
    case = TestCase(**data)
    assert case.need_human_confirm is True
    assert case.missing_information == ["性能阈值"]


def test_serialization_contains_only_canonical_fields() -> None:
    data = _canonical_data()
    data.update(
        {
            "expected_result": "旧预期",
            "pass_criteria": "旧准则",
            "source_document": "old.docx",
        }
    )
    serialized = TestCase.from_legacy_dict(data).to_persistence_dict()
    assert set(serialized) == set(TestCase.model_fields)
    assert "expected_result" not in serialized
    assert "pass_criteria" not in serialized
    assert "source_document" not in serialized
    assert "need_human_confirmation" not in serialized
