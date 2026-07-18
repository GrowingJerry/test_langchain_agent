# -*- coding: utf-8 -*-
"""
后续可接入 Chroma / FAISS / Milvus 的向量检索接口预留。
当前返回空列表，不影响主流程。
"""

from typing import List, Optional
import warnings


def vector_search_cases(
    query_text: str,
    top_k: int = 5,
    collection: Optional[str] = None,
) -> List[str]:
    """Deprecated placeholder; use infrastructure.retrieval.ProjectRetriever."""
    warnings.warn(
        "rag.vector_search.vector_search_cases is deprecated and remains an unaudited placeholder",
        DeprecationWarning,
        stacklevel=2,
    )
    return []
