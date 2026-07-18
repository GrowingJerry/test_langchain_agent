# -*- coding: utf-8 -*-
"""预留：标准知识库（GJB/Z 141 等）RAG 接入点。"""

from pathlib import Path
from typing import List

# 后续可将条款文件置于此目录
KB_ROOT = Path(__file__).resolve().parent


def search_clauses(query: str, top_k: int = 5) -> List[str]:
    """检索标准条款摘要（预留）。"""
    return []
