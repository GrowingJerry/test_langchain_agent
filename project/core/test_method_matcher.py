# -*- coding: utf-8 -*-
"""根据需求文本、场景与六性类别匹配推荐测试方法。"""

from typing import List, Set

from models.schemas import RequirementItem

# (关键词子串列表, 推荐方法描述)
MATCH_RULES = [
    (
        [
            "响应时间",
            "响应时间",
            "延迟",
            "延迟",
            "处理时间",
            "处理时间",
            "识别时间",
            "识别时间",
        ],
        ["计时测试", "重复测试", "边界负载测试"],
    ),
    (
        ["准确率", "识别率", "识别率", "成功率"],
        ["样本集验证测试", "统计分析测试", "重复试验"],
    ),
    (
        ["通信中断", "断电", "断电", "异常恢复", "重连", "重连", "恢复", "恢复"],
        ["故障注入测试", "恢复性测试", "重复试验统计"],
    ),
    (
        ["日志", "日志", "自检", "自检", "诊断", "诊断", "状态上报", "可观测"],
        ["功能验证测试", "日志审查测试", "故障注入测试"],
    ),
    (
        [
            "权限",
            "权限",
            "越权",
            "误操作",
            "访问控制",
            "访问控制",
            "非授权",
            "非授权",
            "防护",
        ],
        ["安全性测试", "误操作测试", "访问控制测试"],
    ),
    (
        [
            "高温",
            "高温",
            "低温",
            "低温",
            "湿热",
            "振动",
            "振动",
            "电磁",
            "盐雾",
            "盐雾",
            "夜间",
            "夜间",
            "低照度",
            "雨雾",
            "雨雾",
            "环境",
            "环境",
        ],
        ["环境适应性试验", "环境条件下功能测试", "场景仿真测试"],
    ),
    (
        ["维修", "维修", "更换", "故障定位", "恢复时间", "平均修复"],
        ["维修性演示", "计时测试", "故障定位测试"],
    ),
    (
        ["备件", "工具", "培训", "手册", "资料"],
        ["保障性审查", "文档审查", "工具适配性检查"],
    ),
    (
        ["无人", "障碍物", "路径规划", "制动", "制动", "避让", "导航"],
        ["场景仿真测试", "实物低速验证测试", "日志审查测试", "安全性验证测试"],
    ),
]

SIX_QUALITY_METHOD_HINTS = {
    "可靠性": ["故障注入测试", "恢复性测试", "重复试验统计"],
    "维修性": ["维修性演示", "计时测试", "故障定位测试"],
    "测试性": ["功能验证测试", "日志审查测试", "故障注入测试"],
    "保障性": ["保障性审查", "文档审查", "工具适配性检查"],
    "安全性": ["安全性测试", "误操作测试", "访问控制测试"],
    "环境适应性": ["环境适应性试验", "环境条件下功能测试", "场景仿真测试"],
    "性能": ["计时测试", "重复测试", "样本集验证测试", "统计分析测试"],
}


def match_test_methods(req: RequirementItem, six_categories: List[str]) -> List[str]:
    """返回去重后的推荐测试方法列表。"""
    text_blob = " ".join(
        [
            req.requirement_text or "",
            req.scenario_environment or "",
            req.initial_condition or "",
            req.trigger_event or "",
            req.evaluation_metrics or "",
        ]
    )
    methods: List[str] = []

    for kws, mths in MATCH_RULES:
        for kw in kws:
            if kw in text_blob:
                methods.extend(mths)
                break

    for cat in six_categories or []:
        for m in SIX_QUALITY_METHOD_HINTS.get(cat, []):
            methods.append(m)

    # 去重保持顺序
    seen: Set[str] = set()
    ordered = []
    for m in methods:
        if m not in seen:
            seen.add(m)
            ordered.append(m)
    if not ordered:
        ordered = ["功能验证测试", "日志审查测试"]
    return ordered


def format_methods_for_prompt(methods: List[str]) -> str:
    """拼成提示词中的可读字符串。"""
    return "、".join(methods)
