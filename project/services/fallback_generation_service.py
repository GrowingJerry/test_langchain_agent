"""Deterministic, project-grounded test-case fallback generation."""

from __future__ import annotations

from typing import Any, Dict, List
from uuid import uuid4

from domain.schemas.test_case import TestCase


class FallbackGenerationService:
    """Generate conservative canonical cases without invoking a model."""

    def generate(
        self,
        contexts: List[Dict[str, Any]],
        case_count: int,
        case_type: str,
        reason: str,
    ) -> List[TestCase]:
        if not contexts:
            return []
        cases: List[TestCase] = []
        for index in range(case_count):
            context = contexts[index % len(contexts)]
            cases.append(self._build_case(context, case_type, reason, index + 1))
        return cases

    def _build_case(
        self,
        context: Dict[str, Any],
        case_type: str,
        reason: str,
        sequence: int,
    ) -> TestCase:
        profile = context.get("project_profile") or {}
        requirement = context.get("requirement") or {}
        scenarios = context.get("related_scenario_cards") or []
        scenario = scenarios[0] if scenarios else {}
        chunks = context.get("related_chunks") or []
        requirement_id = str(
            context.get("requirement_id") or requirement.get("requirement_id") or ""
        )
        scenario_id = str(scenario.get("scenario_id") or "")
        scenario_name = str(scenario.get("scenario_name") or "需求对应场景需人工确认")
        objective = str(
            requirement.get("description")
            or requirement.get("title")
            or "验证当前项目需求"
        )
        test_object = str(
            profile.get("test_object")
            or profile.get("project_name")
            or "当前项目测试对象"
        )
        preconditions = list(
            scenario.get("preconditions")
            or ["按当前项目文档准备测试环境、版本和初始状态"]
        )
        test_data = list(scenario.get("input_data") or [])
        environment = list(
            scenario.get("environment") or profile.get("constraints") or []
        )
        missing = list(context.get("missing_information") or [])
        if not test_data and "场景输入数据" not in missing:
            missing.append("场景输入数据")
        if not scenario_id and "关联场景" not in missing:
            missing.append("关联场景")
        source_chunk_ids = [
            str(row.get("chunk_id")) for row in chunks if row.get("chunk_id")
        ]
        source_documents = list(
            dict.fromkeys(
                str(row.get("filename")) for row in chunks if row.get("filename")
            )
        )
        trigger = str(scenario.get("trigger_event") or objective)
        steps = [
            f"确认{test_object}已按当前项目文档部署，并核对前置条件和初始状态。",
            f"准备项目资料明确给出的测试数据并执行“{trigger}”。",
            f"观察并记录“{scenario_name}”中的界面、接口返回、日志和状态变化。",
            "将实际结果与当前需求及来源片段逐项比对并记录结论。",
        ]
        expected = [
            "测试对象、版本和初始状态满足当前项目文档约定。",
            "系统进入当前需求规定的处理流程；未明确的输入数据需人工确认。",
            "形成可追溯的实际结果、接口响应、日志或状态记录。",
            "实际结果符合当前需求；资料未给出阈值时按需求文档规定值判定。",
        ]
        return TestCase(
            case_id=f"TC-RULE-{uuid4().hex[:12]}",
            title=f"{case_type}-{requirement_id}-{scenario_name}-{sequence}",
            objective=f"验证需求 {requirement_id}：{objective[:120]}",
            preconditions=preconditions,
            test_steps=steps,
            expected_results=expected,
            evaluation_criteria="需人工确认" if missing else "按需求文档规定值判定",
            test_data=test_data,
            environment=environment,
            requirement_ids=[requirement_id] if requirement_id else [],
            scenario_ids=[scenario_id] if scenario_id else [],
            source_chunk_ids=source_chunk_ids,
            source_documents=source_documents,
            quality_category=list(context.get("six_quality_attributes") or []),
            test_method="、".join(context.get("matched_test_methods") or []),
            need_human_confirm=bool(missing),
            missing_information=missing,
            generation_mode="rule_fallback",
        )
