"""Embedding abstraction reusing the existing local Ollama client."""

from __future__ import annotations

from typing import List, Optional, Protocol

from infrastructure.llm.embedding_client import OllamaEmbeddingClient


class EmbeddingProvider(Protocol):
    """Minimal embedding contract required by project retrieval."""

    model: str

    def embed(self, text: str) -> Optional[List[float]]:
        """Return one vector, or ``None`` when embeddings are unavailable."""


class OllamaEmbeddingProvider:
    """Adapter around the existing deterministic-fallback Ollama embedding client."""

    def __init__(self, client: Optional[OllamaEmbeddingClient] = None) -> None:
        self.client = client or OllamaEmbeddingClient()
        self.model = self.client.model

    def embed(self, text: str) -> Optional[List[float]]:
        return self.client.embed(text)
