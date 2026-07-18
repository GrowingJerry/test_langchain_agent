"""Compatibility facade for the project-bound retrieval infrastructure."""

from __future__ import annotations

from typing import Dict, List, Optional

from core.embedding_client import OllamaEmbeddingClient
from core.project_manager import ProjectManager
from infrastructure.retrieval.embeddings import OllamaEmbeddingProvider
from infrastructure.retrieval.hybrid_search import (
    keyword_score,
)
from infrastructure.retrieval.project_retriever import ProjectRetriever


def score_chunk(content: str, tokens: List[str]) -> float:
    """Compatibility alias for the established keyword score."""
    return keyword_score(content, tokens)


def ensure_embeddings_for_chunks(
    manager: ProjectManager,
    project_id: str,
    chunks: Optional[List[Dict[str, object]]] = None,
    embedding_client: Optional[OllamaEmbeddingClient] = None,
) -> int:
    """Generate missing embeddings through a project-bound retriever."""
    provider = (
        OllamaEmbeddingProvider(embedding_client)
        if embedding_client is not None
        else None
    )
    retriever = ProjectRetriever(manager, project_id, provider)
    normalized = [dict(row) for row in chunks] if chunks is not None else None
    return retriever.ensure_embeddings(normalized)


def search_project_chunks(
    manager: ProjectManager,
    project_id: str,
    query: str,
    top_k: int = 5,
) -> List[Dict[str, object]]:
    """Return legacy dictionaries from the project-bound normalized retriever."""
    return [
        row.to_legacy_dict()
        for row in ProjectRetriever(manager, project_id).search(query, top_k)
    ]
