# -*- coding: utf-8 -*-
"""Ollama embedding client with graceful fallback."""

from __future__ import annotations

from typing import List, Optional

import requests

from config.settings import OLLAMA_BASE_URL, OLLAMA_EMBED_MODEL, OLLAMA_TIMEOUT


class OllamaEmbeddingClient:
    """Small client for Ollama embedding APIs."""

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_EMBED_MODEL,
        timeout: int = OLLAMA_TIMEOUT,
    ):
        self.base_url = (base_url or OLLAMA_BASE_URL).rstrip("/")
        self.model = model or OLLAMA_EMBED_MODEL
        self.timeout = timeout

    def embed(self, text: str) -> Optional[List[float]]:
        """Return one embedding vector, or None if Ollama is unavailable."""
        source = (text or "").strip()
        if not source:
            return None
        shortened = source[:4000]
        return self._embed_legacy(shortened) or self._embed_current(shortened)

    def _embed_legacy(self, text: str) -> Optional[List[float]]:
        """Call Ollama's /api/embeddings endpoint."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
                timeout=min(self.timeout, 8),
            )
            resp.raise_for_status()
            data = resp.json()
            embedding = data.get("embedding")
            if isinstance(embedding, list):
                return [float(x) for x in embedding]
        except (requests.RequestException, ValueError, TypeError):
            return None
        return None

    def _embed_current(self, text: str) -> Optional[List[float]]:
        """Call Ollama's /api/embed endpoint."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/embed",
                json={"model": self.model, "input": text},
                timeout=min(self.timeout, 8),
            )
            resp.raise_for_status()
            data = resp.json()
            embeddings = data.get("embeddings")
            if isinstance(embeddings, list) and embeddings:
                first = embeddings[0]
                if isinstance(first, list):
                    return [float(x) for x in first]
            embedding = data.get("embedding")
            if isinstance(embedding, list):
                return [float(x) for x in embedding]
        except (requests.RequestException, ValueError, TypeError):
            return None
        return None
