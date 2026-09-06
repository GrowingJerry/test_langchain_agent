# -*- coding: utf-8 -*-
"""Prompt template for extracting structured visual evidence from images."""

import json
from typing import Any, Dict, Optional


VISUAL_EVIDENCE_FIELDS = [
    "image_type",
    "main_objects",
    "visible_text",
    "possible_functions",
    "possible_test_points",
    "risk_points",
    "source_region",
    "confidence",
    "need_human_confirm",
]


def build_visual_evidence_prompt(asset_context: Optional[Dict[str, Any]] = None) -> str:
    """Build a strict JSON prompt for visual evidence extraction."""
    context = asset_context or {}
    return f"""你是项目级测试文档系统的视觉证据抽取助手。请只从输入图片中抽取可作为测试设计依据的视觉证据，不要生成测试用例。

必须遵守：
1. 只输出一个合法 JSON 对象，不要 Markdown 代码块，不要额外说明。
2. 不要编造图片中不存在的文字、设备名称、接口字段、状态、数值或结论。
3. visible_text 只能填写图片中清晰可见的原文；看不清、被遮挡或不确定的文字不要猜测。
4. 不确定的信息放入 risk_points，并将 need_human_confirm 设置为 true。
5. possible_functions 只能描述图片可支持推断的功能线索，不得扩展为完整需求。
6. possible_test_points 只能列出可供后续测试设计参考的观察点、状态点、输入输出点或异常风险点。
7. 不直接生成测试用例，不输出测试步骤、预期结果或判定准则。
8. source_region 用简短文字说明证据来源区域，例如“整图”“顶部标题栏”“右侧告警区域”“第2页流程图中部”。
9. confidence 使用 0 到 1 的数字；信息越清晰、越可见，置信度越高。

JSON 字段必须至少包括：
{json.dumps(VISUAL_EVIDENCE_FIELDS, ensure_ascii=False)}

字段含义：
- image_type：图片类型，如截图、流程图、扫描页、设备界面图、表格图片、未知。
- main_objects：图片中主要对象或界面元素。
- visible_text：图片中清晰可见的文字原文列表。
- possible_functions：从图片可见信息中谨慎推断的功能线索。
- possible_test_points：后续测试设计可关注的测试点线索。
- risk_points：不确定、看不清、需人工确认或可能影响测试设计的风险点。
- source_region：证据主要来自图片中的哪个区域。
- confidence：整体抽取置信度。
- need_human_confirm：是否需要人工确认。

资产上下文（仅用于定位来源，不得替代图片事实）：
{json.dumps(context, ensure_ascii=False, default=str)}
"""
