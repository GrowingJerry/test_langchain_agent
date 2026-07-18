"""HTTP-only Ollama health and model-discovery client."""

from __future__ import annotations

from typing import Any, List, Optional

import requests

from config.settings import Settings, settings as default_settings


class OllamaHealthClient:
    """Check Ollama availability without performing model inference."""

    def __init__(
        self,
        settings: Settings = default_settings,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        session: Optional[Any] = None,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.timeout = (
            timeout if timeout is not None else min(5, settings.ollama_timeout)
        )
        self._session = session or requests

    def is_available(self) -> bool:
        """Return whether the Ollama tags endpoint responds successfully."""
        try:
            response = self._session.get(
                f"{self.base_url}/api/tags", timeout=self.timeout
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    def list_models(self) -> List[str]:
        """Return installed Ollama model names, or an empty list on health failure."""
        try:
            response = self._session.get(
                f"{self.base_url}/api/tags", timeout=self.timeout
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError, TypeError):
            return []
        models = payload.get("models", []) if isinstance(payload, dict) else []
        return [
            str(item["name"])
            for item in models
            if isinstance(item, dict) and item.get("name")
        ]

    def model_exists(self, model_name: str) -> bool:
        """Return whether an exact model name is installed."""
        target = model_name.strip()
        return bool(target) and target in self.list_models()
