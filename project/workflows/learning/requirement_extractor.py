# -*- coding: utf-8 -*-
"""Conservative requirement extraction from project document chunks."""

from __future__ import annotations

import re
from typing import Dict, List


REQ_KEYWORDS = (
    "需求",
    "应",
    "应当",
    "必须",
    "需要",
    "支持",
    "提供",
    "实现",
    "具备",
    "不得",
    "性能",
    "接口",
    "约束",
)


def classify_requirement(text: str) -> str:
    """Classify extracted requirement text into a broad category."""
    s = text or ""
    if any(k in s for k in ("接口", "API", "报文", "协议", "通信")):
        return "接口需求"
    if any(
        k in s for k in ("性能", "响应", "并发", "吞吐", "时延", "不低于", "不高于")
    ):
        return "性能需求"
    if any(k in s for k in ("安全", "权限", "认证", "加密", "审计")):
        return "安全需求"
    if any(k in s for k in ("约束", "限制", "环境", "版本", "不得")):
        return "约束需求"
    return "功能需求"


def split_candidate_sentences(text: str) -> List[str]:
    """Split a chunk into requirement-like candidate sentences."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    pieces = re.split(r"(?<=[。；;])|[\n\r]+", normalized)
    out = []
    for item in pieces:
        s = item.strip(" -\t")
        if 8 <= len(s) <= 260 and any(k in s for k in REQ_KEYWORDS):
            out.append(s)
    return out


def extract_requirements_from_chunks(
    chunks: List[Dict[str, object]],
) -> List[Dict[str, str]]:
    """Extract project requirements from chunk rows and keep source links."""
    rows: List[Dict[str, str]] = []
    seen = set()
    for chunk in chunks:
        content = str(chunk.get("content") or "")
        filename = str(chunk.get("filename") or chunk.get("document_id") or "")
        chunk_id = str(chunk.get("chunk_id") or "")
        for sentence in split_candidate_sentences(content):
            key = re.sub(r"\s+", "", sentence)
            if key in seen:
                continue
            seen.add(key)
            rid = f"REQ-{len(rows) + 1:03d}"
            title = sentence[:48] + ("..." if len(sentence) > 48 else "")
            rows.append(
                {
                    "requirement_id": rid,
                    "title": title,
                    "description": sentence,
                    "category": classify_requirement(sentence),
                    "source_document": filename,
                    "source_chunk_id": chunk_id,
                }
            )
    return rows
