from typing import Any

from langchain_core.runnables import RunnableLambda

from chains.test_case_review import TestCaseReviewChain as StructuredReviewChain
from config.settings import Settings
from domain.schemas.review import Review
from services.review_service import ReviewService


class FakeManager:
    def __init__(self, case: dict[str, Any]) -> None:
        self.case = case
        self.saved = []

    def list_generated_cases(self, project_id: str) -> list[dict[str, Any]]:
        return [{"case_id": "C1", "case_json": self.case}]

    def save_review_result(self, *args: Any) -> None:
        self.saved.append(args)

    def save_generated_case(self, *args: Any) -> None:
        raise AssertionError("review must not mutate cases")


class FakeStructuredReviewChain:
    def run(self, case_id: str, case: dict[str, Any]) -> Review:
        return Review(
            artifact_id=case_id,
            review_type="llm",
            status="needs_human_confirmation",
            issues=["接口阈值缺失"],
            suggestions=["人工确认阈值"],
            need_human_confirm=True,
        )


class HealthyReviewModel:
    def __init__(self) -> None:
        self.schema: Any = None

    def with_structured_output(self, schema: Any) -> Any:
        self.schema = schema
        return RunnableLambda(
            lambda _: {
                "artifact_id": "model-value",
                "review_type": "llm",
                "status": "needs_human_confirmation",
                "issues": ["缺少环境"],
                "suggestions": [],
                "need_human_confirm": True,
            }
        )


class HealthyClient:
    def is_available(self) -> bool:
        return True

    def model_exists(self, model_name: str) -> bool:
        return True


def test_deterministic_review_uses_canonical_schema_without_mutation() -> None:
    original = {"case_id": "C1", "test_steps": [], "expected_results": []}
    snapshot = dict(original)
    result = ReviewService(FakeManager(original)).deterministic_review("C1", original)
    assert result.status == "needs_revision"
    assert result.review_type == "deterministic"
    assert original == snapshot


def test_structured_llm_review_is_advisory_and_persisted_as_review_only() -> None:
    case = {
        "objective": "验证接口",
        "test_steps": ["调用"],
        "expected_results": ["成功"],
        "evaluation_criteria": "按文档判定",
    }
    manager = FakeManager(case)
    results = ReviewService(manager, FakeStructuredReviewChain()).review_project(
        "P1", include_llm=True
    )
    assert {result.review_type for result in results} == {
        "deterministic",
        "quality",
        "llm",
    }
    assert all(isinstance(result, Review) for result in results)
    assert len(manager.saved) == 3


def test_llm_review_chain_uses_pydantic_structured_output() -> None:
    model = HealthyReviewModel()
    result = StructuredReviewChain(
        Settings(enable_ollama=True), model=model, health_client=HealthyClient()
    ).run("C1", {"objective": "验证"})
    assert model.schema is Review
    assert result.artifact_id == "C1"
    assert result.status == "needs_human_confirmation"
