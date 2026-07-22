# -*- coding: utf-8 -*-
"""需求解析：多行文本、txt、Excel 转为统一 RequirementItem 列表。"""

from pathlib import Path
from typing import List, Optional

import pandas as pd

from domain.schemas.generation import RequirementItem


def _next_req_id(index: int, prefix: str = "REQ") -> str:
    """生成需求编号 REQ-001 形式。"""
    return f"{prefix}-{index:03d}"


def split_requirement_lines(text: str) -> List[str]:
    """按行拆分需求，过滤空行。"""
    if not text or not str(text).strip():
        return []
    lines = []
    for line in str(text).splitlines():
        s = line.strip()
        if s:
            lines.append(s)
    return lines


def parse_plain_text(
    text: str,
    test_object: str = "",
    id_prefix: str = "REQ",
    start_index: int = 1,
) -> List[RequirementItem]:
    """解析纯文本多条需求，自动编号。"""
    items: List[RequirementItem] = []
    lines = split_requirement_lines(text)
    for i, line in enumerate(lines):
        rid = _next_req_id(start_index + i, id_prefix)
        items.append(
            RequirementItem(
                requirement_id=rid,
                requirement_text=line,
                test_object=test_object,
                source_type="requirement",
                priority="中",
            )
        )
    return items


def parse_txt_file(path: Path, **kwargs) -> List[RequirementItem]:
    """读取 txt 文件并解析为需求列表。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_plain_text(text, **kwargs)


def parse_excel_requirements(
    path: Path,
    text_column: Optional[str] = None,
    id_column: Optional[str] = None,
) -> List[RequirementItem]:
    """从 Excel 读取需求：自动探测或使用指定列。"""
    df = pd.read_excel(path)
    if df.empty:
        return []

    cols = [str(c).strip() for c in df.columns]
    col_map = {c.lower(): c for c in cols}

    def pick_col(*candidates: str) -> Optional[str]:
        for cand in candidates:
            for key, orig in col_map.items():
                if cand.lower() in key or key in cand.lower():
                    return orig
        return None

    tid_col = id_column or pick_col("requirement_id", "需求编号", "需求编号", "id")
    txt_col = text_column or pick_col(
        "requirement_text", "需求描述", "需求", "requirement", "文本"
    )
    if txt_col is None and len(df.columns) > 0:
        txt_col = df.columns[0]

    obj_col = pick_col("test_object", "测试对象", "测试对象")

    items: List[RequirementItem] = []
    for idx, row in df.iterrows():
        rid = ""
        if tid_col and tid_col in df.columns:
            v = row.get(tid_col)
            if pd.notna(v) and str(v).strip():
                rid = str(v).strip()
        if not rid:
            rid = _next_req_id(idx + 1)

        text = ""
        if txt_col and txt_col in df.columns:
            v = row.get(txt_col)
            if pd.notna(v):
                text = str(v).strip()

        tobj = ""
        if obj_col and obj_col in df.columns:
            v = row.get(obj_col)
            if pd.notna(v):
                tobj = str(v).strip()

        if text:
            items.append(
                RequirementItem(
                    requirement_id=rid,
                    requirement_text=text,
                    test_object=tobj,
                    source_type="requirement",
                )
            )
    return items


def merge_requirement_with_scenario_fields(
    req: RequirementItem,
    scenario_name: str = "",
    scenario_environment: str = "",
    initial_condition: str = "",
    trigger_event: str = "",
    expected_behavior: str = "",
    evaluation_metrics: str = "",
    six_quality: Optional[List[str]] = None,
) -> RequirementItem:
    """将场景字段合入需求项（用于统一生成输入）。"""
    data = req.model_dump()
    if scenario_name:
        data["scenario_name"] = scenario_name
    if scenario_environment:
        data["scenario_environment"] = scenario_environment
    if initial_condition:
        data["initial_condition"] = initial_condition
    if trigger_event:
        data["trigger_event"] = trigger_event
    if expected_behavior:
        data["expected_behavior"] = expected_behavior
    if evaluation_metrics:
        data["evaluation_metrics"] = evaluation_metrics
    if six_quality is not None:
        data["six_quality_attribute"] = six_quality
    if any(
        [
            scenario_name,
            scenario_environment,
            initial_condition,
            trigger_event,
        ]
    ):
        data["source_type"] = "mixed"
    return RequirementItem(**data)
