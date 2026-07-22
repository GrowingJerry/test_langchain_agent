# -*- coding: utf-8 -*-
"""Local knowledge base for the military software test-case writing standard."""

from pathlib import Path
import re
from typing import List, Tuple

from config.settings import DATA_DIR
from infrastructure.documents.document_parser import read_docx_text


STANDARD_DOCX = DATA_DIR / "军用软件测试用例书写标准.docx"

MANDATORY_RULES = [
    "测试用例名称采用三段式结构：[被测模块]-[功能点]-[测试场景类型]-[序号]。",
    "测试用例编号必须唯一，推荐格式为 TC-[模块缩写]-[四位数字序号]；如组织已有编号规范，应优先遵循。",
    "测试用例必须与需求建立正向和逆向追溯关系，确保每个软件需求被覆盖。",
    "正常测试用例、边界测试用例、异常测试用例必须拆分为独立用例，不得混写在同一用例中。",
    "测试输入必须写入具体数值或明确数据，不允许使用“有效值”“正常输入”等模糊描述。",
    "测试规程采用一步一验证：每个步骤只描述一个可执行动作，并对应一个可观察的预期结果。",
    "界面操作步骤应写清页面、区域、控件和动作；控件名称使用 〖〗 包裹。",
    "涉及等待的步骤必须明确等待时长或等待条件。",
    "预期结果必须具体、可观察、可量化；性能类用例必须使用具体指标。",
    "通过准则必须可判定、可验证，不得使用主观描述。",
    "每个测试用例应保持独立，不依赖其他用例执行结果。",
    "先决条件应写明测试环境、软件版本、硬件配置、测试数据和环境校验状态。",
    "假设与约束须逐条列出，并明确标注“假设”或“约束”。",
]


def load_standard_text(path: Path = STANDARD_DOCX) -> str:
    """Load the source standard text from data/*.docx."""
    if not path.exists():
        return ""
    return read_docx_text(path)


def standard_sections(path: Path = STANDARD_DOCX) -> List[Tuple[str, str]]:
    """Split the standard into numbered sections while preserving full content."""
    text = load_standard_text(path)
    if not text:
        return []
    sections: List[Tuple[str, str]] = []
    title = "全文开头"
    lines: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^(?:[一二三四五六七八九十]+、|\d+(?:\.\d+)+\s*)", line):
            if lines:
                sections.append((title, "\n".join(lines)))
            title = line[:80]
            lines = [line]
        else:
            lines.append(line)
    if lines:
        sections.append((title, "\n".join(lines)))
    return sections


def standard_summary() -> str:
    """Return a compact rules summary suitable for prompts and UI display."""
    return "\n".join(f"{idx}. {rule}" for idx, rule in enumerate(MANDATORY_RULES, 1))


def standard_full_reference(max_chars: int = 24000) -> str:
    """Return the full standard text, trimmed only when it exceeds prompt limits."""
    text = load_standard_text()
    if not text:
        return standard_summary()
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + "\n（标准全文较长，此处已按提示词长度截断；源文件仍为 data/军用软件测试用例书写标准.docx）"
    )


def standard_reference_for_prompt(query: str = "", max_chars: int = 18000) -> str:
    """Build prompt context from the full standard, not a hand-written summary.

    The standard is about 16k chars in the current project, so by default the
    entire document is included. If it grows beyond the prompt budget, relevant
    numbered sections are selected and the mandatory summary is appended.
    """
    full = load_standard_text()
    if not full:
        full = standard_summary()
    if len(full) <= max_chars:
        return (
            "以下内容来自 data/军用软件测试用例书写标准.docx，作为最高优先级依据：\n"
            + full
        )

    clauses = search_standard_clauses(query, top_k=20)
    selected = "\n\n".join(clauses)
    if len(selected) > max_chars:
        selected = selected[:max_chars]
    return (
        "以下内容来自 data/军用软件测试用例书写标准.docx 的全文检索结果，"
        "生成时必须遵守；不得仅按摘要发挥：\n"
        f"{selected}\n\n强制规则兜底：\n{standard_summary()}"
    )


def search_standard_clauses(query: str, top_k: int = 8) -> List[str]:
    """Search over the full standard sections and paragraphs."""
    sections = standard_sections()
    if not sections:
        return MANDATORY_RULES[:top_k]

    tokens = [t for t in re.split(r"\W+", query or "") if len(t) >= 2]
    scored = []
    for title, body in sections:
        content = body.strip()
        score = 0
        for token in tokens:
            if token in content:
                score += 2
            if token in title:
                score += 2
        score += content.count("【强制】")
        if score:
            scored.append((score, content))

    if not scored:
        return MANDATORY_RULES[:top_k]
    scored.sort(key=lambda x: (-x[0], len(x[1])))
    return [p for _, p in scored[:top_k]]
