"""Project-scoped retrieval infrastructure."""

from infrastructure.retrieval.embeddings import (
    EmbeddingProvider,
    OllamaEmbeddingProvider,
)
from infrastructure.retrieval.project_retriever import ProjectRetriever

__all__ = ["EmbeddingProvider", "OllamaEmbeddingProvider", "ProjectRetriever"]
