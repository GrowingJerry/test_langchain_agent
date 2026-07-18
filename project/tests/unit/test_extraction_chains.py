"""Unit tests for extraction generation metadata without a live Ollama service."""

from langchain_core.runnables import RunnableLambda

from chains.profile_extraction import ProfileExtractionChain
from chains.scenario_extraction import ScenarioExtractionChain
from config.settings import Settings
from domain.schemas.project import ProjectProfile
from domain.schemas.scenario import ScenarioCard


class Health:
    def __init__(self, available: bool) -> None:
        self.available = available

    def is_available(self) -> bool:
        return self.available


class StructuredModel:
    def __init__(self, result) -> None:
        self.result = result

    def with_structured_output(self, schema):
        return RunnableLambda(lambda _: self.result)


class FailingStructuredModel:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def with_structured_output(self, schema):
        def fail(_):
            raise self.error

        return RunnableLambda(fail)


def fallback_profile(text: str, hint: str) -> ProjectProfile:
    return ProjectProfile(
        project_name=hint, test_object="规则对象", generation_mode="rule_fallback"
    )


def test_profile_model_disabled_records_fallback_reason() -> None:
    result = ProfileExtractionChain(Settings(enable_ollama=False)).run(
        "资料", "项目", fallback_profile
    )
    assert result.generation_mode == "rule_fallback"
    assert result.failure_type == "model_disabled"


def test_profile_structured_output() -> None:
    expected = ProjectProfile(project_name="结构化项目", test_object="系统")
    chain = ProfileExtractionChain(Settings(), StructuredModel(expected), Health(True))
    result = chain.run("资料", "提示", fallback_profile)
    assert result.project_name == "结构化项目"
    assert result.generation_mode == "structured_output"


def test_profile_structured_failure_preserves_error_context() -> None:
    chain = ProfileExtractionChain(
        Settings(), FailingStructuredModel(ValueError("invalid schema")), Health(True)
    )
    result = chain.run("资料", "提示", fallback_profile)
    assert result.failure_type == "structured_output_error"
    assert "invalid schema" in result.failure_message


def test_profile_timeout_records_failure_type() -> None:
    chain = ProfileExtractionChain(
        Settings(), FailingStructuredModel(TimeoutError("slow model")), Health(True)
    )
    result = chain.run("资料", "提示", fallback_profile)
    assert result.failure_type == "timeout"
    assert "slow model" in result.failure_message


def test_scenario_unavailable_records_fallback_reason() -> None:
    def fallback(project_id, chunks, requirements):
        return [
            ScenarioCard(
                scenario_id="S1", project_id=project_id, scenario_name="规则场景"
            )
        ]

    result = ScenarioExtractionChain(Settings(), health_client=Health(False)).run(
        "P1", [], [], fallback
    )
    assert result.generation_mode == "rule_fallback"
    assert result.failure_type == "model_unavailable"
