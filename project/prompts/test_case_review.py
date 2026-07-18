"""Prompt template for constrained structured test-case review."""

from langchain_core.prompts import ChatPromptTemplate


def test_case_review_prompt() -> ChatPromptTemplate:
    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是测试用例审查器。只审查完整性、可执行性、可判定性、来源充分性和是否编造指标。不得修改原用例。信息不足时标记需人工确认。只返回结构化结果。",
            ),
            ("human", "请审查以下当前项目测试用例：\n{case_json}"),
        ]
    )
