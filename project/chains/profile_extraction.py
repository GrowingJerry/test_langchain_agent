"""Structured project-profile extraction chain with deterministic fallback."""

from __future__ import annotations

import logging
from typing import Callable, Optional

import httpx
from ollama import ResponseError
import requests
from langchain_core.exceptions import OutputParserException
from pydantic import ValidationError

from config.settings import Settings, settings as default_settings
from domain.exceptions import (
    ConfigurationError,
    ModelUnavailableError,
    StructuredOutputError,
)
from domain.schemas.project import ExtractedProjectProfile, ProjectProfile
from infrastructure.llm.model_factory import OllamaModelFactory
from infrastructure.llm.ollama_health import OllamaHealthClient
from prompts.profile_extraction import profile_extraction_prompt


LOGGER = logging.getLogger(__name__)
FallbackCallable = Callable[[str, str], ProjectProfile]


class ProfileExtractionChain:
    """Run ChatOllama structured extraction and expose explicit fallback metadata."""

    def __init__(
        self,
        settings: Settings = default_settings,
        model: Optional[object] = None,
        health_client: Optional[OllamaHealthClient] = None,
    ) -> None:
        self.settings = settings
        self._model = model
        self.health_client = health_client or OllamaHealthClient(settings)

    def run(
        self, text: str, project_name_hint: str, fallback: FallbackCallable
    ) -> ProjectProfile:
        source = (text or "").strip()
        if not self.settings.enable_ollama:
            return self._fallback(
                fallback,
                source,
                project_name_hint,
                "model_disabled",
                "ENABLE_OLLAMA=false",
            )
        if not self.health_client.is_available():
            return self._fallback(
                fallback,
                source,
                project_name_hint,
                "model_unavailable",
                "Ollama health check failed",
            )
        try:
            model = self._model or OllamaModelFactory(self.settings).extraction_model()
            runnable = profile_extraction_prompt() | model.with_structured_output(
                ExtractedProjectProfile
            )
            result = runnable.invoke(
                {
                    "project_name_hint": project_name_hint,
                    "document_text": source[:18000],
                }
            )
            extracted = (
                result
                if isinstance(result, ExtractedProjectProfile)
                else ExtractedProjectProfile.model_validate(result)
            )
            profile_data = extracted.model_dump()
            profile_data["project_name"] = (
                extracted.project_name or project_name_hint.strip()
            )
            return ProjectProfile(**profile_data, generation_mode="structured_output")
        except (TimeoutError, requests.Timeout, httpx.TimeoutException) as exc:
            LOGGER.exception("Project profile extraction timed out")
            return self._fallback(
                fallback, source, project_name_hint, "timeout", repr(exc)
            )
        except ResponseError as exc:
            failure_type = (
                "model_unavailable"
                if exc.status_code < 0 or exc.status_code >= 500
                else "structured_output_error"
            )
            detail = self._error_detail(exc)
            LOGGER.warning(
                "Project profile Ollama request failed; using rule fallback: %s",
                detail,
            )
            return self._fallback(
                fallback,
                source,
                project_name_hint,
                failure_type,
                detail,
            )
        except (
            requests.ConnectionError,
            httpx.ConnectError,
            ModelUnavailableError,
            ConfigurationError,
            OSError,
        ) as exc:
            LOGGER.exception("Project profile model is unavailable")
            return self._fallback(
                fallback, source, project_name_hint, "model_unavailable", repr(exc)
            )
        except (
            ValidationError,
            ValueError,
            OutputParserException,
            StructuredOutputError,
        ) as exc:
            LOGGER.exception("Project profile structured output validation failed")
            detail = self._error_detail(exc)
            return self._fallback(
                fallback,
                source,
                project_name_hint,
                "structured_output_error",
                detail,
            )
        except Exception as exc:
            LOGGER.exception("Unexpected project profile extraction failure")
            raise StructuredOutputError(
                f"Unexpected profile extraction failure: {exc!r}"
            ) from exc

    @staticmethod
    def _error_detail(exc: Exception) -> str:
        if isinstance(exc, ResponseError):
            return f"ResponseError(status_code={exc.status_code}, error={exc.error!r})"
        return repr(exc)

    @staticmethod
    def _fallback(
        fallback: FallbackCallable,
        text: str,
        hint: str,
        failure_type: str,
        failure_message: str,
    ) -> ProjectProfile:
        result = fallback(text, hint)
        return result.model_copy(
            update={
                "generation_mode": "rule_fallback",
                "failure_type": failure_type,
                "failure_message": failure_message,
            }
        )
