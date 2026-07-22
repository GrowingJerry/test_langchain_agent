"""Unified factories for local ChatOllama models."""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from config.settings import Settings, settings as default_settings
from domain.exceptions import ConfigurationError

try:
    from langchain_ollama import ChatOllama
except ImportError:  # pragma: no cover - exercised through the explicit guard
    ChatOllama = None  # type: ignore[assignment,misc]


class ModelPurpose(str, Enum):
    """Supported local chat-model purposes."""

    TEXT = "text"
    EXTRACTION = "extraction"
    REVIEW = "review"


class OllamaModelFactory:
    """Create consistently configured ChatOllama instances without connecting eagerly."""

    def __init__(self, settings: Settings = default_settings) -> None:
        self.settings = settings

    def create(
        self,
        purpose: ModelPurpose = ModelPurpose.TEXT,
        temperature: Optional[float] = None,
        timeout: Optional[int] = None,
        max_retries: Optional[int] = None,
        streaming: Optional[bool] = None,
        num_ctx: Optional[int] = None,
    ) -> Any:
        """Create one ChatOllama model for the requested purpose."""
        if not self.settings.enable_ollama:
            raise ConfigurationError("Ollama is disabled by ENABLE_OLLAMA")
        if ChatOllama is None:
            raise ConfigurationError("langchain-ollama is not installed")

        model_name, default_temperature = self._purpose_defaults(purpose)
        request_timeout = (
            timeout if timeout is not None else self.settings.ollama_timeout
        )
        retry_count = (
            max_retries if max_retries is not None else self.settings.ollama_max_retries
        )
        stream_enabled = (
            streaming if streaming is not None else self.settings.enable_streaming
        )
        effective_temperature = (
            temperature if temperature is not None else default_temperature
        )
        context_window = num_ctx if num_ctx is not None else self.settings.ollama_num_ctx
        fixed_structured_task = purpose in {ModelPurpose.EXTRACTION, ModelPurpose.REVIEW}
        return ChatOllama(
            model=model_name,
            base_url=self.settings.ollama_base_url,
            temperature=effective_temperature,
            max_retries=retry_count,
            disable_streaming=not stream_enabled,
            validate_model_on_init=False,
            num_ctx=context_window,
            num_predict=(
                self.settings.ollama_structured_num_predict
                if fixed_structured_task
                else None
            ),
            # Tool/structured tasks need an answer or tool call, not a separate
            # thinking block. qwen3 can otherwise keep reasoning after it has
            # gathered all facts and never submit the response schema.
            reasoning=False,
            client_kwargs={"timeout": request_timeout},
        )

    def text_model(self, **overrides: Any) -> Any:
        return self.create(ModelPurpose.TEXT, **overrides)

    def extraction_model(self, **overrides: Any) -> Any:
        return self.create(ModelPurpose.EXTRACTION, **overrides)

    def review_model(self, **overrides: Any) -> Any:
        return self.create(ModelPurpose.REVIEW, **overrides)

    def _purpose_defaults(self, purpose: ModelPurpose) -> tuple[str, float]:
        if purpose == ModelPurpose.EXTRACTION:
            return (
                self.settings.ollama_extraction_model,
                self.settings.ollama_extraction_temperature,
            )
        if purpose == ModelPurpose.REVIEW:
            return (
                self.settings.ollama_review_model,
                self.settings.ollama_review_temperature,
            )
        return self.settings.ollama_model, self.settings.ollama_temperature


def create_text_model(settings: Settings = default_settings, **overrides: Any) -> Any:
    return OllamaModelFactory(settings).text_model(**overrides)


def create_extraction_model(
    settings: Settings = default_settings, **overrides: Any
) -> Any:
    return OllamaModelFactory(settings).extraction_model(**overrides)


def create_review_model(settings: Settings = default_settings, **overrides: Any) -> Any:
    return OllamaModelFactory(settings).review_model(**overrides)
