"""Canonical test type registry used by extraction and generation UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class TestType:
    code: str
    label: str
    aliases: tuple[str, ...] = ()


TEST_TYPES: tuple[TestType, ...] = (
    TestType("functional", "功能测试", ("功能", "function", "functional")),
    TestType("performance", "性能测试", ("性能", "performance", "perf")),
    TestType("interface", "接口测试", ("接口", "协议", "API", "interface", "api")),
    TestType("exception", "异常测试", ("异常", "错误处理", "exception")),
    TestType("security", "安全性测试", ("安全", "权限", "认证", "加密", "security")),
    TestType("reliability", "可靠性测试", ("可靠", "容错", "恢复", "MTBF", "reliability")),
    TestType("compatibility", "兼容性测试", ("兼容", "适配", "compatibility")),
    TestType("installation", "安装测试", ("安装", "部署", "升级", "卸载")),
    TestType("usability", "界面/易用性测试", ("界面", "易用", "布局", "提示")),
    TestType("static", "静态测试", ("静态", "代码审查", "静态分析")),
    TestType("environmental", "环境适应性测试", ("环境", "温度", "湿度", "冲击", "电磁")),
)

LABELS: List[str] = [item.label for item in TEST_TYPES]
_ALIAS_TO_LABEL: Dict[str, str] = {}
for item in TEST_TYPES:
    _ALIAS_TO_LABEL[item.code.casefold()] = item.label
    _ALIAS_TO_LABEL[item.label.casefold()] = item.label
    for alias in item.aliases:
        _ALIAS_TO_LABEL[alias.casefold()] = item.label


def normalize_test_type(value: object) -> str:
    """Return the canonical label for a known test type, otherwise an empty string."""
    text = str(value or "").strip()
    if not text:
        return ""
    folded = text.casefold()
    if folded in _ALIAS_TO_LABEL:
        return _ALIAS_TO_LABEL[folded]
    for alias, label in _ALIAS_TO_LABEL.items():
        if alias and alias in folded:
            return label
    return ""


def is_numeric_test_type(value: object) -> bool:
    return str(value or "").strip() in {"1", "2", "3", "4", "5", "6", "7", "8", "9"}
