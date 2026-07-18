# -*- coding: utf-8 -*-
"""场景解析：表单或 Excel 转为 ScenarioItem，并可合并为生成输入。"""

from pathlib import Path
from typing import List, Optional

import pandas as pd

from models.schemas import RequirementItem, ScenarioItem


def _pick_column(df: pd.DataFrame, *candidates: str) -> Optional[str]:
    """按候选名模糊匹配列名。"""
    cols = {str(c).strip().lower(): c for c in df.columns}
    for cand in candidates:
        cl = cand.lower()
        for k, orig in cols.items():
            if cl in k or k in cl:
                return orig
    return None


def scenario_from_form(
    scenario_id: str,
    requirement_id: str,
    scenario_name: str,
    scenario_environment: str,
    initial_condition: str,
    trigger_event: str,
    expected_behavior: str,
    evaluation_metrics: str,
    test_object: str,
    six_quality_text: str = "",
) -> ScenarioItem:
    """从 Streamlit 表单字段构建场景。"""
    six_list = [
        s.strip() for s in six_quality_text.replace("，", ",").split(",") if s.strip()
    ]
    return ScenarioItem(
        scenario_id=scenario_id or f"SCE-{requirement_id or 'NA'}",
        requirement_id=requirement_id,
        scenario_name=scenario_name,
        scenario_environment=scenario_environment,
        initial_condition=initial_condition,
        trigger_event=trigger_event,
        expected_behavior=expected_behavior,
        evaluation_metrics=evaluation_metrics,
        test_object=test_object,
        six_quality_attribute=six_list,
    )


def parse_scenarios_excel(path: Path) -> List[ScenarioItem]:
    """从 Excel 读取多条场景。"""
    df = pd.read_excel(path)
    if df.empty:
        return []

    def cell(row, col: Optional[str]) -> str:
        if not col or col not in df.columns:
            return ""
        v = row.get(col)
        if pd.isna(v):
            return ""
        return str(v).strip()

    col_sid = _pick_column(df, "scenario_id", "场景编号", "场景编号")
    col_rid = _pick_column(df, "requirement_id", "需求编号", "需求编号")
    col_name = _pick_column(df, "scenario_name", "场景名称", "场景名称", "名称")
    col_env = _pick_column(df, "scenario_environment", "场景环境", "场景环境", "环境")
    col_init = _pick_column(df, "initial_condition", "初始条件", "初始条件")
    col_trig = _pick_column(df, "trigger_event", "触发事件", "触发事件")
    col_exp = _pick_column(df, "expected_behavior", "期望行为", "期望行为")
    col_eval = _pick_column(df, "evaluation_metrics", "评价指标", "评价指标")
    col_obj = _pick_column(df, "test_object", "测试对象", "测试对象")
    col_six = _pick_column(df, "six_quality_attribute", "六性", "关注六性")

    out: List[ScenarioItem] = []
    for i, row in df.iterrows():
        sid = cell(row, col_sid) or f"SCE-{i + 1:03d}"
        rid = cell(row, col_rid)
        six_raw = cell(row, col_six)
        six_list = [
            s.strip()
            for s in six_raw.replace("、", ",").replace("，", ",").split(",")
            if s.strip()
        ]
        out.append(
            ScenarioItem(
                scenario_id=sid,
                requirement_id=rid,
                scenario_name=cell(row, col_name) or sid,
                scenario_environment=cell(row, col_env),
                initial_condition=cell(row, col_init),
                trigger_event=cell(row, col_trig),
                expected_behavior=cell(row, col_exp),
                evaluation_metrics=cell(row, col_eval),
                test_object=cell(row, col_obj),
                six_quality_attribute=six_list,
            )
        )
    return out


def scenario_to_requirement_like(s: ScenarioItem) -> RequirementItem:
    """将独立场景转为可生成用例的“类需求”输入（需求文本由场景摘要生成）。"""
    parts = [
        f"场景：{s.scenario_name}" if s.scenario_name else "",
        f"环境：{s.scenario_environment}" if s.scenario_environment else "",
        f"初始条件：{s.initial_condition}" if s.initial_condition else "",
        f"触发事件：{s.trigger_event}" if s.trigger_event else "",
        f"期望行为：{s.expected_behavior}" if s.expected_behavior else "",
        f"评价指标：{s.evaluation_metrics}" if s.evaluation_metrics else "",
    ]
    merged = "；".join(p for p in parts if p)
    rid = s.requirement_id or f"REQ-SCE-{s.scenario_id}"
    return RequirementItem(
        requirement_id=rid,
        requirement_text=merged or s.scenario_name,
        test_object=s.test_object,
        scenario_name=s.scenario_name,
        scenario_environment=s.scenario_environment,
        initial_condition=s.initial_condition,
        trigger_event=s.trigger_event,
        expected_behavior=s.expected_behavior,
        evaluation_metrics=s.evaluation_metrics,
        six_quality_attribute=s.six_quality_attribute or [],
        source_type="scenario",
    )


def bind_scenario_to_requirement(
    req: RequirementItem, sce: ScenarioItem
) -> RequirementItem:
    """将场景绑定到指定需求（覆写场景字段）。"""
    return RequirementItem(
        requirement_id=req.requirement_id,
        requirement_text=req.requirement_text,
        test_object=sce.test_object or req.test_object,
        scenario_name=sce.scenario_name,
        scenario_environment=sce.scenario_environment,
        initial_condition=sce.initial_condition,
        trigger_event=sce.trigger_event,
        expected_behavior=sce.expected_behavior or req.expected_behavior,
        evaluation_metrics=sce.evaluation_metrics or req.evaluation_metrics,
        six_quality_attribute=sce.six_quality_attribute or req.six_quality_attribute,
        priority=req.priority,
        source_type="mixed",
    )
