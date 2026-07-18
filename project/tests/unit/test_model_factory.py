"""Tests for ChatOllama factory configuration without a live server."""

from unittest.mock import Mock

import pytest

from config.settings import Settings
from domain.exceptions import ConfigurationError
from infrastructure.llm import model_factory


def test_chat_ollama_initialization_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    constructor = Mock(return_value=Mock())
    monkeypatch.setattr(model_factory, "ChatOllama", constructor)
    settings = Settings(
        ollama_base_url="http://ollama:11434",
        ollama_model="text",
        ollama_extraction_model="extract",
        ollama_review_model="review",
        ollama_timeout=33,
        ollama_num_ctx=8192,
        ollama_structured_num_predict=1536,
        ollama_max_retries=4,
        enable_streaming=True,
    )
    model_factory.OllamaModelFactory(settings).extraction_model(temperature=0.1)
    constructor.assert_called_once_with(
        model="extract",
        base_url="http://ollama:11434",
        temperature=0.1,
        max_retries=4,
        disable_streaming=False,
        validate_model_on_init=False,
        num_ctx=8192,
        num_predict=1536,
        reasoning=False,
        client_kwargs={"timeout": 33},
    )


def test_disabled_ollama_does_not_construct_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    constructor = Mock()
    monkeypatch.setattr(model_factory, "ChatOllama", constructor)
    factory = model_factory.OllamaModelFactory(Settings(enable_ollama=False))
    with pytest.raises(ConfigurationError, match="disabled"):
        factory.text_model()
    constructor.assert_not_called()
