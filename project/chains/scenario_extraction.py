"""Structured project-scenario extraction chain with deterministic fallback."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

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
from domain.schemas.scenario import (
    ScenarioCard,
    ScenarioExtractionOutput,
    ScenarioExtractionResult,
)
from infrastructure.llm.model_factory import OllamaModelFactory
from infrastructure.llm.ollama_health import OllamaHealthClient
from workflows.scenario.prompts import scenario_extraction_prompt


LOGGER = logging.getLogger(__name__)
ScenarioFallback = Callable[
    [str, List[Dict[str, Any]], List[Dict[str, Any]]], List[ScenarioCard]
]


class ScenarioExtractionChain:
    """Extract grounded scenarios and validate all model-provided project references."""

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
        self,
        project_id: str,
        chunks: List[Dict[str, Any]],
        requirements: List[Dict[str, Any]],
        fallback: ScenarioFallback,
    ) -> ScenarioExtractionResult:
        if not self.settings.enable_ollama:
            return self._fallback(
                fallback,
                project_id,
                chunks,
                requirements,
                "model_disabled",
                "ENABLE_OLLAMA=false",
            )
        if not self.health_client.is_available():
            return self._fallback(
                fallback,
                project_id,
                chunks,
                requirements,
                "model_unavailable",
                "Ollama health check failed",
            )
        source = [
            {
                "source_document": row.get("filename", ""),
                "source_chunk_id": row.get("chunk_id", ""),
                "content": str(row.get("content") or "")[:1200],
            }
            for row in chunks[:30]
        ]
        reqs = [
            {
                "requirement_id": row.get("requirement_id", ""),
                "description": row.get("description", ""),
            }
            for row in requirements
        ]
        try:
            model = self._model or OllamaModelFactory(self.settings).extraction_model()
            runnable = scenario_extraction_prompt() | model.with_structured_output(
                ScenarioExtractionOutput
            )
            raw = runnable.invoke(
                {
                    "project_id": project_id,
                    "requirements_json": json.dumps(reqs, ensure_ascii=False),
                    "chunks_json": json.dumps(source, ensure_ascii=False),
                }
            )
            output = (
                raw
                if isinstance(raw, ScenarioExtractionOutput)
                else ScenarioExtractionOutput.model_validate(raw)
            )
            cards = self._scope_cards(
                project_id, output.scenario_cards, chunks, requirements
            )
            return ScenarioExtractionResult(
                scenario_cards=cards, generation_mode="structured_output"
            )
        except (TimeoutError, requests.Timeout, httpx.TimeoutException) as exc:
            LOGGER.exception("Scenario extraction timed out")
            return self._fallback(
                fallback, project_id, chunks, requirements, "timeout", repr(exc)
            )
        except ResponseError as exc:
            failure_type = (
                "model_unavailable"
                if exc.status_code < 0 or exc.status_code >= 500
                else "structured_output_error"
            )
            detail = self._error_detail(exc)
            LOGGER.warning(
                "Scenario extraction Ollama request failed; using rule fallback: %s",
                detail,
            )
            return self._fallback(
                fallback,
                project_id,
                chunks,
                requirements,
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
            LOGGER.exception("Scenario extraction model is unavailable")
            return self._fallback(
                fallback,
                project_id,
                chunks,
                requirements,
                "model_unavailable",
                repr(exc),
            )
        except (
            ValidationError,
            ValueError,
            OutputParserException,
            StructuredOutputError,
        ) as exc:
            LOGGER.exception("Scenario structured output validation failed")
            return self._fallback(
                fallback,
                project_id,
                chunks,
                requirements,
                "structured_output_error",
                self._error_detail(exc),
            )
        except Exception as exc:
            LOGGER.exception("Unexpected scenario extraction failure")
            raise StructuredOutputError(
                f"Unexpected scenario extraction failure: {exc!r}"
            ) from exc

    @staticmethod
    def _error_detail(exc: Exception) -> str:
        if isinstance(exc, ResponseError):
            return f"ResponseError(status_code={exc.status_code}, error={exc.error!r})"
        return repr(exc)

    @staticmethod
    def _scope_cards(
        project_id: str,
        cards: List[ScenarioCard],
        chunks: List[Dict[str, Any]],
        requirements: List[Dict[str, Any]],
    ) -> List[ScenarioCard]:
        valid_chunks = {str(row.get("chunk_id") or "") for row in chunks}
        valid_requirements = {
            str(row.get("requirement_id") or "") for row in requirements
        }
        scoped: List[ScenarioCard] = []
        for card in cards:
            chunk_ids = [item for item in card.source_chunk_ids if item in valid_chunks]
            requirement_ids = [
                item for item in card.related_requirements if item in valid_requirements
            ]
            scoped.append(
                card.model_copy(
                    update={
                        "scenario_id": card.scenario_id or f"SCN-{uuid4().hex[:12]}",
                        "project_id": project_id,
                        "source_chunk_ids": chunk_ids,
                        "related_requirements": requirement_ids,
                        "need_human_confirm": bool(
                            card.need_human_confirm or not chunk_ids
                        ),
                    }
                )
            )
        return scoped

    @staticmethod
    def _fallback(
        fallback: ScenarioFallback,
        project_id: str,
        chunks: List[Dict[str, Any]],
        requirements: List[Dict[str, Any]],
        failure_type: str,
        failure_message: str,
    ) -> ScenarioExtractionResult:
        return ScenarioExtractionResult(
            scenario_cards=fallback(project_id, chunks, requirements),
            generation_mode="rule_fallback",
            failure_type=failure_type,
            failure_message=failure_message,
        )
