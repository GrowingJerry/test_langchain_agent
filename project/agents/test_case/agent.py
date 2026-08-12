"""LangChain v1 test-case Agent facade with finite calls and no persistence side effects."""

from __future__ import annotations

import json
from typing import Any, Callable, List, Optional

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

    def generate(
        self,
        request: TestCaseAgentRequest,
        progress_callback: Callable[[dict[str, str]], None] | None = None,
    ) -> GeneratedCaseBundle:
        """Run one finite Agent invocation; callers own deterministic fallback behavior."""
        self.runtime.reset_observations()
        prompt = build_generation_request_prompt(
            request.requirement_ids,
            request.case_count,
            request.case_type,
            request.additional_instructions,
            request.scenario_ids,
            request.auto_case_count,
        )
        prefetched = self._prefetch_required_context(request)
        if prefetched:
            prompt += (
                "\n以下是运行时通过当前项目只读工具预取的实际结果。不得重复调用这些"
                "目标工具；只能使用其中事实：\n"
                + json.dumps(prefetched, ensure_ascii=False, default=str)[:24000]
            )
        try:
            graph_input = {"messages": [{"role": "user", "content": prompt}]}
            graph_config = {
                    # LangGraph counts model and tool supersteps separately. The
                    # middleware remains the authoritative finite call budget.
                    "recursion_limit": (
                        self.runtime.settings.agent_max_model_calls
                        + self.runtime.settings.agent_max_tool_calls
                        + 5
                    )
                }
            if progress_callback is None:
                state = self.graph.invoke(graph_input, config=graph_config)
            else:
                state = self._stream_graph(graph_input, graph_config, progress_callback)
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
                    "Test-case Agent recursion limit exceeded after tools "
                    f"{self.runtime.used_tool_names}: {exc}"
                ) from exc
            raise AgentExecutionError(
                f"Test-case Agent execution failed: {exc!r}"
            ) from exc

        structured = (
            state.get("structured_response") if isinstance(state, dict) else None
        )
        if structured is None:
            messages = state.get("messages", []) if isinstance(state, dict) else []
            last_message = messages[-1] if messages else None
            structured = self._validated_local_json(last_message)
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
        if any(
            len(case.test_steps) != len(case.expected_results)
            for case in bundle.cases
        ):
            bundle = self._repair_structured_bundle(bundle, request)
        return self._apply_observed_provenance(bundle, request)

    def _stream_graph(
        self,
        graph_input: dict[str, Any],
        graph_config: dict[str, Any],
        callback: Callable[[dict[str, str]], None],
    ) -> dict[str, Any]:
        """Consume LangGraph token/update streams and retain the final structured state."""
        state: dict[str, Any] = {}
        for item in self.graph.stream(
            graph_input, config=graph_config, stream_mode=["messages", "updates"]
        ):
            mode, data = item if isinstance(item, tuple) and len(item) == 2 else ("updates", item)
            if mode == "messages":
                message = data[0] if isinstance(data, tuple) else data
                content = getattr(message, "content", "")
                if isinstance(content, str) and content:
                    callback({"kind": "token", "content": content})
                reasoning = (getattr(message, "additional_kwargs", {}) or {}).get("reasoning_content")
                if reasoning:
                    callback({"kind": "reasoning", "content": str(reasoning)})
                continue
            if isinstance(data, dict):
                for node_update in data.values():
                    if isinstance(node_update, dict):
                        state.update(node_update)
                callback({"kind": "status", "content": "模型正在整理结构化测试用例…"})
        return state

    def _prefetch_required_context(
        self, request: TestCaseAgentRequest
    ) -> List[dict[str, Any]]:
        """Deterministically obtain mandatory target facts before model planning."""
        tools = {item.name: item for item in self.tools}
        results: List[dict[str, Any]] = []
        for scenario_id in request.scenario_ids:
            for name in (
                "get_compiled_scenario",
                "get_scenario_equipment_allocation",
                "get_scenario_validation_result",
            ):
                results.append({
                    "tool": name,
                    "result": tools[name].invoke({"scenario_id": scenario_id}),
                })
        for requirement_id in request.requirement_ids:
            results.append({
                "tool": "get_requirement_context",
                "result": tools["get_requirement_context"].invoke({
                    "requirement_id": requirement_id
                }),
            })
            results.append({
                "tool": "get_related_scenarios",
                "result": tools["get_related_scenarios"].invoke({
                    "requirement_id": requirement_id
                }),
            })
        if len(results) > self.runtime.settings.agent_max_tool_calls:
            raise AgentCallLimitError(
                "Mandatory target prefetch exceeds the configured tool-call limit"
            )
        return results

    @staticmethod
    def _validated_local_json(message: Any) -> GeneratedCaseBundle:
        """Validate JSON emitted as plain text by local tool-capable models."""
        content = str(getattr(message, "content", "") or "").strip()
        if content.startswith("```"):
            content = content.removeprefix("```json").removeprefix("```")
            content = content.removesuffix("```").strip()
        try:
            return GeneratedCaseBundle.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
            raise StructuredOutputError(
                "Test-case Agent returned neither ToolStrategy output nor valid "
                f"GeneratedCaseBundle JSON: {exc}"
            ) from exc

    def _repair_structured_bundle(
        self, bundle: GeneratedCaseBundle, request: TestCaseAgentRequest
    ) -> GeneratedCaseBundle:
        """Deterministically remove unpaired tails without inventing content."""
        repaired_cases = []
        warnings = list(bundle.warnings)
        for case in bundle.cases[:request.case_count]:
            pair_count = min(len(case.test_steps), len(case.expected_results))
            if pair_count <= 0:
                raise StructuredOutputError(
                    f"Case {case.case_id} has no complete step/result pair"
                )
            warnings.append(
                f"用例 {case.case_id} 的步骤与预期结果数量不一致；"
                "已确定性删除无对应关系的尾部内容"
            )
            repaired_cases.append(case.model_copy(update={
                "test_steps": case.test_steps[:pair_count],
                "expected_results": case.expected_results[:pair_count],
                "need_human_confirm": True,
                "missing_information": list(dict.fromkeys([
                    *case.missing_information, "步骤/预期结果尾部已截断，需人工确认"
                ])),
            }))
        return bundle.model_copy(update={
            "cases": repaired_cases,
            "warnings": list(dict.fromkeys(warnings)),
        })

    def _apply_observed_provenance(
        self,
        bundle: GeneratedCaseBundle,
        request: TestCaseAgentRequest,
    ) -> GeneratedCaseBundle:
        """Replace model-claimed tool/source metadata with observed current-project provenance."""
        # Model-declared source IDs are untrusted. Request requirement IDs come
        # from the user; all other provenance comes from project-bound tools.
        observed_requirement_ids = list(dict.fromkeys(request.requirement_ids))
        observed_chunk_ids = list(
            dict.fromkeys(self.runtime.retrieved_source_chunk_ids)
        )
        observed_documents = list(
            dict.fromkeys(self.runtime.retrieved_source_documents)
        )
        observed_scenario_ids = list(
            dict.fromkeys(self.runtime.retrieved_scenario_ids)
        )
        warnings = list(bundle.warnings)
        cases = []
        for case in bundle.cases:
            requirement_ids = observed_requirement_ids
            source_chunk_ids = observed_chunk_ids
            source_documents = observed_documents
            scenario_ids = observed_scenario_ids
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
