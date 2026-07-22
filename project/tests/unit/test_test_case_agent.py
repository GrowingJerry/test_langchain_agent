"""Unit tests for the LangChain v1 test-case Agent using fake chat models."""

from __future__ import annotations

from typing import Any, List, Sequence

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from agents.test_case.agent import TestCaseAgent
from agents.test_case.context import AgentRuntimeContext
from agents.test_case.output_schema import TestCaseAgentRequest
from agents.test_case.tools import build_test_case_tools
from config.settings import Settings
from domain.exceptions import AgentCallLimitError, AgentExecutionError
from domain.schemas.retrieval import ProjectChunkResult


def case_payload(**updates: Any) -> dict:
    data = {
        "case_id": "TC-1",
        "title": "订单提交",
        "objective": "验证订单提交需求。",
        "preconditions": ["服务已启动"],
        "test_steps": ["提交订单"],
        "expected_results": ["系统接受订单"],
        "evaluation_criteria": "按需求文档规定值判定",
        "test_data": ["需求规定的订单数据"],
        "environment": [],
        "requirement_ids": ["REQ-1"],
        "scenario_ids": ["SCN-1"],
        "source_chunk_ids": ["CHK-1"],
        "source_documents": ["req.txt"],
        "quality_category": ["功能性"],
        "test_method": "功能验证测试",
        "need_human_confirm": False,
        "missing_information": [],
        "generation_mode": "agent",
    }
    data.update(updates)
    return data


def bundle_payload(case: dict | None = None) -> dict:
    return {
        "cases": [case or case_payload()],
        "overall_missing_information": [],
        "used_tool_names": [],
        "retrieved_source_chunk_ids": [],
        "warnings": [],
    }


class ToolCallingFakeModel(FakeMessagesListChatModel):
    """Fake model that supports LangChain tool binding and scripted messages."""

    bound_tool_names: List[str] = []

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any):
        self.bound_tool_names = [
            str(
                getattr(item, "name", "")
                or (
                    item.get("function", {}).get("name", "")
                    if isinstance(item, dict)
                    else ""
                )
            )
            for item in tools
        ]
        return self


class FailingFakeModel(ToolCallingFakeModel):
    def _generate(self, *args: Any, **kwargs: Any):
        raise RuntimeError("fake model failure")


class FakeRetriever:
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id

    def search(self, query: str, top_k: int = 5) -> List[ProjectChunkResult]:
        return [
            ProjectChunkResult(
                chunk_id="CHK-1",
                project_id=self.project_id,
                document_id="DOC-1",
                document_name="req.txt",
                content="系统应接受订单。",
                score=88.0,
                retrieval_method="keyword",
            )
        ]


class FakeManager:
    def __init__(self, fail_profile: bool = False) -> None:
        self.calls: List[tuple[str, str]] = []
        self.fail_profile = fail_profile

    def get_profile(self, project_id: str) -> dict:
        self.calls.append(("profile", project_id))
        if self.fail_profile:
            raise OSError("profile storage unavailable")
        return {"project_name": "P1", "test_object": "订单系统", "interfaces": []}

    def get_requirement(self, project_id: str, requirement_id: str) -> dict:
        self.calls.append(("requirement", project_id))
        return {
            "requirement_id": requirement_id,
            "description": "系统应接受订单。",
            "source_document": "req.txt",
            "source_chunk_id": "CHK-1",
        }

    def list_scenario_cards(
        self, project_id: str, requirement_id: str = ""
    ) -> List[dict]:
        self.calls.append(("scenario", project_id))
        return [
            {
                "scenario_id": "SCN-1",
                "scenario_name": "提交订单",
                "source_chunk_ids": ["CHK-1"],
                "need_human_confirm": False,
            }
        ]


class FakeLibrary:
    def search_similar_cases(self, **kwargs: Any):
        item = type(
            "Reference",
            (),
            {
                "case_id": "LIB-1",
                "test_method": "边界值分析",
                "test_steps": "历史项目专有步骤",
                "pass_criteria": "历史项目阈值 9ms",
            },
        )()
        return [(item, 80.0, "方法相似")]


def settings(**updates: Any) -> Settings:
    values = {
        "agent_max_model_calls": 6,
        "agent_max_tool_calls": 6,
        "agent_model_max_retries": 0,
        "agent_tool_max_retries": 0,
    }
    values.update(updates)
    return Settings(**values)


def runtime(
    manager: FakeManager | None = None, case_library: Any = None, **setting_updates: Any
) -> AgentRuntimeContext:
    return AgentRuntimeContext(
        project_id="P1",
        manager=manager or FakeManager(),
        case_library=case_library,
        settings=settings(**setting_updates),
        retriever=FakeRetriever("P1"),
    )


def test_agent_selects_retrieval_tool_and_validates_output() -> None:
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_project_documents",
                        "args": {"query": "订单", "top_k": 3},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_related_scenarios",
                        "args": {"requirement_id": "REQ-1"},
                        "id": "call-2",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "GeneratedCaseBundle",
                        "args": bundle_payload(),
                        "id": "final-1",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
    )
    agent = TestCaseAgent(runtime(), model)
    result = agent.generate(
        TestCaseAgentRequest(requirement_ids=["REQ-1"], case_count=1)
    )
    assert "search_project_documents" in result.used_tool_names
    assert result.retrieved_source_chunk_ids == ["CHK-1"]
    assert result.cases[0].source_chunk_ids == ["CHK-1"]
    assert result.cases[0].scenario_ids == ["SCN-1"]


def test_tools_expose_no_project_id_and_only_use_bound_project() -> None:
    manager = FakeManager()
    context = runtime(manager)
    tools = {item.name: item for item in build_test_case_tools(context)}
    assert all("project_id" not in item.args for item in tools.values())
    tools["get_project_profile"].invoke({})
    tools["get_requirement_context"].invoke({"requirement_id": "REQ-1"})
    tools["get_related_scenarios"].invoke({"requirement_id": "REQ-1"})
    assert manager.calls
    assert all(project_id == "P1" for _, project_id in manager.calls)


def test_observed_prefetch_sources_replace_model_omissions() -> None:
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_project_profile",
                        "args": {},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "GeneratedCaseBundle",
                        "args": bundle_payload(
                            case_payload(
                                scenario_ids=[],
                                source_chunk_ids=[],
                                source_documents=[],
                            )
                        ),
                        "id": "final-1",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
    )
    result = TestCaseAgent(runtime(), model).generate(
        TestCaseAgentRequest(requirement_ids=["REQ-1"])
    )
    assert result.cases[0].need_human_confirm is False
    assert result.cases[0].scenario_ids == ["SCN-1"]
    assert result.cases[0].source_chunk_ids == ["CHK-1"]


def test_history_tool_returns_reference_only_without_project_facts() -> None:
    tool_map = {
        item.name: item
        for item in build_test_case_tools(runtime(case_library=FakeLibrary()))
    }
    result = tool_map["get_reference_cases"].invoke({"query": "订单", "top_k": 2})
    assert result["reference_only"] is True
    assert "仅作测试方法和写作格式参考" in result["notice"]
    serialized = str(result)
    assert "历史项目专有步骤" not in serialized
    assert "历史项目阈值" not in serialized
    assert "source_chunk" not in serialized


def test_tool_exception_is_re_raised_as_agent_error() -> None:
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_project_profile",
                        "args": {},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            )
        ]
    )
    with pytest.raises(AgentExecutionError, match="profile storage unavailable"):
        TestCaseAgent(runtime(FakeManager(fail_profile=True)), model).generate(
            TestCaseAgentRequest(requirement_ids=["REQ-1"])
        )


def test_model_exception_is_re_raised() -> None:
    model = FailingFakeModel(responses=[])
    with pytest.raises(AgentExecutionError, match="fake model failure"):
        TestCaseAgent(runtime(), model).generate(
            TestCaseAgentRequest(requirement_ids=["REQ-1"])
        )


def test_model_call_limit_is_enforced() -> None:
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_project_profile",
                        "args": {},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "GeneratedCaseBundle",
                        "args": bundle_payload(),
                        "id": "final-1",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
    )
    with pytest.raises(AgentCallLimitError):
        TestCaseAgent(runtime(agent_max_model_calls=1), model).generate(
            TestCaseAgentRequest(requirement_ids=["REQ-1"])
        )


def test_tool_call_limit_is_enforced() -> None:
    model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_project_profile",
                        "args": {},
                        "id": "call-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_requirement_context",
                        "args": {"requirement_id": "REQ-1"},
                        "id": "call-2",
                        "type": "tool_call",
                    }
                ],
            ),
        ]
    )
    with pytest.raises(AgentCallLimitError):
        TestCaseAgent(runtime(agent_max_tool_calls=1), model).generate(
            TestCaseAgentRequest(requirement_ids=["REQ-1"])
        )


def test_misaligned_step_result_tails_are_removed_without_model_facts() -> None:
    agent = TestCaseAgent(runtime(), ToolCallingFakeModel(responses=[]))
    bundle = agent._validated_local_json(AIMessage(content=__import__("json").dumps(
        bundle_payload(case_payload(
            test_steps=["步骤一", "没有对应结果的步骤"],
            expected_results=["结果一"],
        ))
    )))

    repaired = agent._repair_structured_bundle(
        bundle, TestCaseAgentRequest(requirement_ids=["REQ-1"])
    )

    assert repaired.cases[0].test_steps == ["步骤一"]
    assert repaired.cases[0].expected_results == ["结果一"]
    assert repaired.cases[0].need_human_confirm is True
    assert "步骤/预期结果尾部已截断，需人工确认" in repaired.cases[0].missing_information
