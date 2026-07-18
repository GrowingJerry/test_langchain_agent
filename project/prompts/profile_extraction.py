"""Prompt template for project-profile structured extraction."""

from langchain_core.prompts import ChatPromptTemplate


PROFILE_SYSTEM_PROMPT = """你是项目资料画像抽取器。只能使用提供的当前项目资料。
禁止补造接口、性能阈值、环境参数或项目事实。资料未明确的字段保持空字符串或空列表。
输出必须满足指定的结构化 schema。"""

PROFILE_USER_PROMPT = """项目名称提示：{project_name_hint}

当前项目资料：
{document_text}"""


def profile_extraction_prompt() -> ChatPromptTemplate:
    """Build the profile extraction chat prompt."""
    return ChatPromptTemplate.from_messages(
        [("system", PROFILE_SYSTEM_PROMPT), ("human", PROFILE_USER_PROMPT)]
    )
