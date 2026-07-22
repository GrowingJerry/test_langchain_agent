# -*- coding: utf-8 -*-
"""Compatibility facade for structured project-profile extraction."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from chains.profile_extraction import ProfileExtractionChain
from config.settings import settings
from infrastructure.llm.ollama_client import OllamaClient
from domain.schemas.project import ProjectProfile


PROFILE_KEYS = [
    "project_name",
    "domain",
    "test_object",
    "main_functions",
    "interfaces",
    "quality_attributes",
    "constraints",
]


def _as_list(value: Any) -> List[str]:
    """Normalize a JSON value into a list of strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        parts = re.split(r"[、,，;\n；]+", value)
        return [p.strip() for p in parts if p.strip()]
    return [str(value).strip()]


def _find_lines(text: str, keywords: List[str], limit: int = 8) -> List[str]:
    """Find short source lines containing any keyword."""
    rows = []
    for line in re.split(r"[\r\n]+", text or ""):
        s = re.sub(r"\s+", " ", line).strip()
        if 4 <= len(s) <= 180 and any(k in s for k in keywords):
            rows.append(s)
        if len(rows) >= limit:
            break
    return rows


def _fallback_profile_model(text: str, project_name_hint: str = "") -> ProjectProfile:
    """Extract a conservative profile when LLM output is unavailable."""
    name = project_name_hint.strip()
    if not name:
        m = re.search(
            r"(?:项目名称|系统名称|软件名称)[:：]\s*([^\n\r]{2,60})", text or ""
        )
        name = m.group(1).strip() if m else "未命名项目"

    domain_lines = _find_lines(text, ["业务", "领域", "应用", "场景", "系统"])
    object_lines = _find_lines(text, ["测试对象", "被测", "系统", "软件", "模块"])
    function_lines = _find_lines(
        text, ["功能", "支持", "实现", "提供", "管理", "查询", "导入", "导出"]
    )
    interface_lines = _find_lines(
        text, ["接口", "API", "协议", "报文", "通信", "HTTP", "TCP", "UDP"]
    )
    quality_lines = _find_lines(
        text, ["性能", "可靠", "安全", "易用", "维护", "兼容", "效率"]
    )
    constraint_lines = _find_lines(
        text, ["约束", "限制", "必须", "不得", "应当", "环境", "版本"]
    )

    return ProjectProfile(
        project_name=name,
        domain=domain_lines[0] if domain_lines else "",
        test_object=object_lines[0] if object_lines else "",
        main_functions=function_lines,
        interfaces=interface_lines,
        quality_attributes=quality_lines,
        constraints=constraint_lines,
        generation_mode="rule_fallback",
    )


def fallback_extract_profile(text: str, project_name_hint: str = "") -> Dict[str, Any]:
    """Compatibility rule extractor returning the established dictionary shape."""
    return _fallback_profile_model(text, project_name_hint).model_dump()


def extract_project_profile(
    text: str,
    project_name_hint: str = "",
    ollama: Optional[OllamaClient] = None,
    use_ollama: bool = True,
) -> Dict[str, Any]:
    """Extract a profile through the structured chain or deterministic fallback."""
    source = (text or "").strip()
    runtime_settings = (
        settings if use_ollama else settings.model_copy(update={"enable_ollama": False})
    )
    result = ProfileExtractionChain(runtime_settings).run(
        source,
        project_name_hint,
        _fallback_profile_model,
    )
    return result.model_dump()
