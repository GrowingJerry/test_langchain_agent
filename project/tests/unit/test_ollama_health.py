"""Tests for HTTP-only Ollama health checks."""

from unittest.mock import Mock

import requests

from config.settings import Settings
from infrastructure.llm.ollama_health import OllamaHealthClient


def test_health_check_success_and_model_list() -> None:
    response = Mock(status_code=200)
    response.json.return_value = {
        "models": [{"name": "qwen3:8b"}, {"name": "nomic-embed-text"}]
    }
    session = Mock()
    session.get.return_value = response
    client = OllamaHealthClient(Settings(), session=session)
    assert client.is_available() is True
    assert client.list_models() == ["qwen3:8b", "nomic-embed-text"]
    assert client.model_exists("qwen3:8b") is True


def test_health_check_failure() -> None:
    session = Mock()
    session.get.side_effect = requests.ConnectionError("offline")
    client = OllamaHealthClient(Settings(), session=session)
    assert client.is_available() is False
    assert client.list_models() == []


def test_model_does_not_exist() -> None:
    response = Mock(status_code=200)
    response.json.return_value = {"models": [{"name": "qwen3:8b"}]}
    session = Mock()
    session.get.return_value = response
    client = OllamaHealthClient(Settings(), session=session)
    assert client.model_exists("missing:latest") is False
