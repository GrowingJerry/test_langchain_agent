"""LangChain v1 test-case Agent facade with finite calls and no persistence side effects."""

from __future__ import annotations

from typing import Any, List, Optional

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    ModelRetryMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
)
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain.agents.structured_output import ToolStrategy
from pydantic import ValidationError

from agents.test_case.context import AgentRuntimeContext
from agents.test_case.output_schema import GeneratedCaseBundle, TestCaseAgentRequest
from agents.test_case.prompts import (
    TEST_CASE_AGENT_SYSTEM_PROMPT,
    build_generation_request_prompt,
)
from agents.test_case.tools import build_test_case_tools
from domain.exceptions import (
    AgentCallLimitError,
    AgentExecutionError,
    StructuredOutputError,
)
from infrastructure.llm.model_factory import OllamaModelFactory


class TestCaseAgent:
    """Generate structured cases using read-only tools bound to one project."""

    __test__ = False

    def __init__(
        self, runtime: AgentRuntimeContext, model: Optional[Any] = None
    ) -> None:
        self.runtime = runtime
        self.model = model or OllamaModelFactory(runtime.settings).text_model()
        self.tools = build_test_case_tools(runtime)
        self.graph = create_agent(
            model=self.model,
            tools=self.tools,
            system_prompt=TEST_CASE_AGENT_SYSTEM_PROMPT,
            response_format=ToolStrategy(
                GeneratedCaseBundle,
                tool_message_content="GeneratedCaseBundle 已通过结构化校验。",
                handle_errors="结构化输出不符合 GeneratedCaseBundle，请仅修正字段后重试。",
            ),
            middleware=self._middleware(),
            name="project_test_case_agent",
        )

    def _middleware(self) -> List[Any]:
        settings = self.runtime.settings
        return [
            ModelCallLimitMiddleware(
                run_limit=settings.agent_max_model_calls,
                exit_behavior="error",
            ),
            ToolCallLimitMiddleware(
                run_limit=settings.agent_max_tool_calls,
                exit_behavior="error",
            ),
            ModelRetryMiddleware(
                max_retries=settings.agent_model_max_retries,
                on_failure="error",
                initial_delay=0.0,
                backoff_factor=0.0,
                jitter=False,
            ),
            ToolRetryMiddleware(
                max_retries=settings.agent_tool_max_retries,
                on_failure="error",
                initial_delay=0.0,
                backoff_factor=0.0,
                jitter=False,
            ),
        ]

    def generate(self, request: TestCaseAgentRequest) -> GeneratedCaseBundle:
        """Run one finite Agent invocation; callers own deterministic fallback behavior."""
        self.runtime.reset_observations()
        prompt = build_generation_request_prompt(
            request.requirement_ids,
            request.case_count,
            request.case_type,
            request.additional_instructions,
            request.scenario_ids,
        )
        try:
            state = self.graph.invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config={
                    "recursion_limit": self.runtime.settings.agent_max_model_calls * 2
                    + 3
                },
            )
        except (ModelCallLimitExceededError, ToolCallLimitExceededError) as exc:
            raise AgentCallLimitError(
                f"Test-case Agent call limit exceeded: {exc}"
            ) from exc
        except ValidationError as exc:
            raise StructuredOutputError(
                f"GeneratedCaseBundle validation failed: {exc}"
            ) from exc
        except Exception as exc:
            if exc.__class__.__name__ == "GraphRecursionError":
                raise AgentCallLimitError(
                    f"Test-case Agent recursion limit exceeded: {exc}"
                ) from exc
            raise AgentExecutionError(
                f"Test-case Agent execution failed: {exc!r}"
            ) from exc

        structured = (
            state.get("structured_response") if isinstance(state, dict) else None
        )
        if structured is None:
            raise StructuredOutputError(
                "Test-case Agent returned no structured_response"
            )
        try:
            bundle = (
                structured
                if isinstance(structured, GeneratedCaseBundle)
                else GeneratedCaseBundle.model_validate(structured)
            )
        except ValidationError as exc:
            raise StructuredOutputError(
                f"GeneratedCaseBundle validation failed: {exc}"
            ) from exc
        if len(bundle.cases) > request.case_count:
            raise StructuredOutputError(
                f"Agent produced {len(bundle.cases)} cases, exceeding requested limit {request.case_count}"
            )
        return self._apply_observed_provenance(bundle, request)

    def _apply_observed_provenance(
        self,
        bundle: GeneratedCaseBundle,
        request: TestCaseAgentRequest,
    ) -> GeneratedCaseBundle:
        """Replace model-claimed tool/source metadata with observed current-project provenance."""
        requested_requirements = set(request.requirement_ids)
        allowed_chunks = set(self.runtime.retrieved_source_chunk_ids)
        allowed_documents = set(self.runtime.retrieved_source_documents)
        allowed_scenarios = set(self.runtime.retrieved_scenario_ids)
        warnings = list(bundle.warnings)
        cases = []
        for case in bundle.cases:
            requirement_ids = [
                item for item in case.requirement_ids if item in requested_requirements
            ]
            source_chunk_ids = [
                item for item in case.source_chunk_ids if item in allowed_chunks
            ]
            source_documents = [
                item for item in case.source_documents if item in allowed_documents
            ]
            scenario_ids = [
                item for item in case.scenario_ids if item in allowed_scenarios
            ]
            missing = list(case.missing_information)
            if request.requirement_ids and not requirement_ids:
                missing.append("关联需求")
            if not scenario_ids:
                missing.append("关联场景")
            if not source_chunk_ids:
                missing.append("来源片段")
            missing = list(dict.fromkeys(missing))
            if len(case.test_steps) != len(case.expected_results):
                raise StructuredOutputError(
                    f"Case {case.case_id} has {len(case.test_steps)} steps but "
                    f"{len(case.expected_results)} expected results"
                )
            cases.append(
                case.model_copy(
                    update={
                        "requirement_ids": requirement_ids,
                        "scenario_ids": scenario_ids,
                        "source_chunk_ids": source_chunk_ids,
                        "source_documents": source_documents,
                        "missing_information": missing,
                        "need_human_confirm": bool(case.need_human_confirm or missing),
                    }
                )
            )
        if not self.runtime.used_tool_names:
            warnings.append("Agent 未成功调用任何项目工具，结果必须人工确认。")
        overall_missing = list(
            dict.fromkeys(
                list(bundle.overall_missing_information)
                + [item for case in cases for item in case.missing_information]
            )
        )
        return bundle.model_copy(
            update={
                "cases": cases,
                "overall_missing_information": overall_missing,
                "used_tool_names": list(self.runtime.used_tool_names),
                "retrieved_source_chunk_ids": list(
                    self.runtime.retrieved_source_chunk_ids
                ),
                "knowledge_unit_ids": list(
                    self.runtime.retrieved_knowledge_unit_ids
                ),
                "equipment_ids": list(self.runtime.retrieved_equipment_ids),
                "configuration_rule_ids": list(
                    self.runtime.retrieved_configuration_rule_ids
                ),
                "scenario_validation_run_id": (
                    self.runtime.retrieved_scenario_validation_run_ids[-1]
                    if self.runtime.retrieved_scenario_validation_run_ids
                    else ""
                ),
                "warnings": list(dict.fromkeys(warnings)),
            }
        )
