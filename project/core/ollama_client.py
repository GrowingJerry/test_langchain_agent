# -*- coding: utf-8 -*-
"""Ollama 本地 API 客户端：对话、JSON 生成、连线检测与容错解析。"""

import json
from typing import Any, List, Optional

from config.settings import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT
from infrastructure.llm.ollama_health import OllamaHealthClient


class OllamaClient:
    """封装 Ollama HTTP API，支援 chat 与严格 JSON 输出。"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self.base_url = (base_url or OLLAMA_BASE_URL).rstrip("/")
        self.model = model or OLLAMA_MODEL
        self.timeout = timeout if timeout is not None else OLLAMA_TIMEOUT
        self._health = OllamaHealthClient(
            base_url=self.base_url, timeout=min(5, self.timeout)
        )

    def extract_json(self, text: str) -> Optional[Any]:
        """Deprecated exact JSON decoder; never extracts or repairs model text."""
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def check_connection(self) -> bool:
        """兼容别名；新代码使用 ``OllamaHealthClient.is_available``。"""
        return self.is_available()

    def is_available(self) -> bool:
        """委托给只负责健康检查的客户端。"""
        return self._health.is_available()

    def list_models(self) -> List[str]:
        """委托给只负责模型发现的客户端。"""
        return self._health.list_models()
