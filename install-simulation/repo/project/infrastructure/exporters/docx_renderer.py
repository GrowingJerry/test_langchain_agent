# -*- coding: utf-8 -*-
"""预留：根据 document_placeholders 批量渲染 Word（docxtpl / python-docx）。"""

from pathlib import Path
from typing import List


def render_from_placeholders(
    placeholders_excel: Path,
    template_dir: Path,
    output_dir: Path,
) -> List[Path]:
    """读取占位符表批量生成 docx（第一版为空实现）。"""
    return []
