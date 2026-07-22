"""Prompt template for project scenario structured extraction."""

from langchain_core.prompts import ChatPromptTemplate


SCENARIO_SYSTEM_PROMPT = """你是项目使用场景抽取器。只能使用提供的当前项目需求和文档片段。
禁止补造接口、环境、状态或性能指标。缺失信息保持为空，并设置 need_human_confirm=true。
source_chunk_ids 和 related_requirements 只能取自输入中给出的标识符。
输出必须满足指定的结构化 schema。"""

SCENARIO_USER_PROMPT = """当前项目 ID：{project_id}

当前项目需求：
{requirements_json}

当前项目文档片段：
{chunks_json}"""


def scenario_extraction_prompt() -> ChatPromptTemplate:
    """Build the scenario extraction chat prompt."""
    return ChatPromptTemplate.from_messages(
        [("system", SCENARIO_SYSTEM_PROMPT), ("human", SCENARIO_USER_PROMPT)]
    )
