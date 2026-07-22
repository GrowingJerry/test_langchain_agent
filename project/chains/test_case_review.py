"""Optional structured-output Chain for test-case review."""

import json
from typing import Any, Optional

from config.settings import Settings, settings as default_settings
from domain.exceptions import ModelUnavailableError, StructuredOutputError
from domain.schemas.review import Review
from infrastructure.llm.model_factory import OllamaModelFactory
from infrastructure.llm.ollama_health import OllamaHealthClient
from agents.test_case.review_prompt import test_case_review_prompt


class TestCaseReviewChain:
    def __init__(
        self,
        settings: Settings = default_settings,
        model: Optional[Any] = None,
        health_client: Optional[OllamaHealthClient] = None,
    ) -> None:
        self.settings = settings
        self.model = model
        self.health = health_client or OllamaHealthClient(settings)

    def run(self, case_id: str, case: dict[str, Any]) -> Review:
        if (
            not self.settings.enable_ollama
            or not self.health.is_available()
            or not self.health.model_exists(self.settings.ollama_review_model)
        ):
            raise ModelUnavailableError("Ollama review model is unavailable")
        try:
            model = self.model or OllamaModelFactory(self.settings).review_model(
                streaming=False
            )
            result = (
                test_case_review_prompt() | model.with_structured_output(Review)
            ).invoke({"case_json": json.dumps(case, ensure_ascii=False)})
            review = (
                result if isinstance(result, Review) else Review.model_validate(result)
            )
            return review.model_copy(
                update={"artifact_id": case_id, "review_type": "llm"}
            )
        except (ModelUnavailableError, StructuredOutputError):
            raise
        except Exception as exc:
            raise StructuredOutputError(f"Structured review failed: {exc}") from exc
