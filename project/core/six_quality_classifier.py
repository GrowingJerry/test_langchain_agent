# -*- coding: utf-8 -*-
"""六性与性能关键词规则分类，可选 Ollama 辅助修正。"""

import warnings
from typing import List, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from config.settings import settings
from core.ollama_client import OllamaClient
from infrastructure.llm.model_factory import OllamaModelFactory
from models.schemas import RequirementItem

# 规则：类别名 -> 关键词列表
RULE_KEYWORDS = {
    "可靠性": [
        "连续运行",
        "连续运行",
        "故障",
        "恢复",
        "恢复",
        "重启",
        "重启",
        "中断",
        "失效",
        "成功率",
        "稳定",
        "稳定",
        "异常恢复",
        "断电",
        "重连",
        "重连",
        "断电",
    ],
    "维修性": [
        "维修",
        "维修",
        "更换",
        "更换",
        "维护",
        "维护",
        "故障定位",
        "恢复时间",
        "平均修复时间",
        "模块替换",
        "模组替换",
    ],
    "测试性": [
        "自检",
        "自检",
        "日志",
        "日志",
        "诊断",
        "诊断",
        "状态上报",
        "测试接口",
        "可观测",
        "故障注入",
        "记录",
        "记录",
    ],
    "保障性": [
        "备件",
        "备件",
        "工具",
        "培训",
        "培训",
        "保障资源",
        "维护手册",
        "操作手册",
        "技术资料",
        "技术资料",
    ],
    "安全性": [
        "权限",
        "权限",
        "误操作",
        "告警",
        "危险",
        "危险",
        "防护",
        "越权",
        "访问控制",
        "访问控制",
        "安全状态",
        "碰撞",
        "制动",
        "制动",
        "非授权",
        "非授权",
    ],
    "环境适应性": [
        "高温",
        "高温",
        "低温",
        "低温",
        "湿热",
        "湿热",
        "振动",
        "振动",
        "冲击",
        "冲击",
        "电磁",
        "电磁",
        "盐雾",
        "盐雾",
        "低气压",
        "低气压",
        "环境",
        "环境",
        "夜间",
        "夜间",
        "低照度",
        "雨雾",
        "雨雾",
    ],
    "性能": [
        "响应时间",
        "响应时间",
        "准确率",
        "识别率",
        "识别率",
        "延迟",
        "延迟",
        "吞吐量",
        "并发",
        "并发",
        "处理时间",
        "处理时间",
        "帧率",
        "帧率",
        "识别时间",
        "识别时间",
    ],
}


def rule_classify(text: str) -> List[str]:
    """根据关键词规则返回多个六性／性能类别。"""
    if not text:
        return []
    hits = []
    for cat, kws in RULE_KEYWORDS.items():
        for kw in kws:
            if kw in text:
                if cat not in hits:
                    hits.append(cat)
                break
    if not hits:
        hits = ["可靠性"]
    return hits


class _QualityCategoryOutput(BaseModel):
    categories: List[str] = Field(default_factory=list)


def _ollama_refine_categories(
    client: OllamaClient,
    requirement_text: str,
    rule_result: List[str],
) -> List[str]:
    """Use a bounded structured-output Chain to refine deterministic categories."""
    try:
        configured = settings.model_copy(
            update={
                "enable_ollama": True,
                "ollama_base_url": client.base_url,
                "ollama_model": client.model,
            }
        )
        model = OllamaModelFactory(configured).text_model(streaming=False)
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", "将需求分类到可靠性、维修性、测试性、保障性、安全性、环境适应性、性能。只返回结构化类别。"),
                ("human", "需求：{requirement}\n规则初判：{rule_result}"),
            ]
        )
        data = (prompt | model.with_structured_output(_QualityCategoryOutput)).invoke(
            {"requirement": requirement_text, "rule_result": "、".join(rule_result)}
        )
        if isinstance(data, _QualityCategoryOutput):
            # 统一简体类名与我们字典一致
            norm_map = {
                "维修性": "维修性",
                "测试性": "测试性",
                "环境适应性": "环境适应性",
            }
            out = []
            for x in data.categories:
                s = str(x).strip()
                s = norm_map.get(s, s)
                if s in RULE_KEYWORDS and s not in out:
                    out.append(s)
            return out if out else rule_result
    except Exception as exc:
        warnings.warn(
            f"Structured quality classification unavailable; using rule result: {type(exc).__name__}: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
    return rule_result


def classify_requirement(
    req: RequirementItem,
    use_ollama: bool = False,
    client: Optional[OllamaClient] = None,
) -> List[str]:
    """对单条需求分类：先规则，可选 Ollama。"""
    blob = " ".join(
        [
            req.requirement_text or "",
            req.scenario_environment or "",
            req.evaluation_metrics or "",
            req.expected_behavior or "",
        ]
    )
    rule = rule_classify(blob)
    if use_ollama and client and client.check_connection():
        return _ollama_refine_categories(client, blob, rule)
    return rule


def apply_manual_categories(
    req: RequirementItem, categories: List[str]
) -> RequirementItem:
    """应用用户在界面手动修改后的类别。"""
    data = req.model_dump()
    data["six_quality_attribute"] = categories
    return RequirementItem(**data)
