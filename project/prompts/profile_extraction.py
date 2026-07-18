"""Prompt template for project-profile structured extraction."""

from langchain_core.prompts import ChatPromptTemplate


PROFILE_SYSTEM_PROMPT = """你是项目资料画像抽取器。只能使用提供的当前项目资料。
禁止补造接口、性能阈值、环境参数或项目事实。资料未明确的字段保持空字符串或空列表。

请按语义分类，不要把所有条目集中到一个字段：
- main_functions：系统提供的业务能力、操作、处理流程和功能模块；
- interfaces：外部接口、通信协议、报文、端口或交互系统；
- quality_attributes：资料明确提出的性能、安全、可靠性、易用性、维护性要求；
- constraints：部署环境、软硬件条件、资源限制、法规标准和明确判定约束。

同一事实只放入最匹配的字段。功能描述不得仅因为含有“应当”“必须”而归入 constraints。
保留资料中的具体名称，但不要复制整段原文；每项应是简洁、独立、可追溯的摘要。
输出必须满足指定的结构化 schema。"""

PROFILE_USER_PROMPT = """项目名称提示：{project_name_hint}

当前项目资料：
{document_text}"""


def profile_extraction_prompt() -> ChatPromptTemplate:
    """Build the profile extraction chat prompt."""
    return ChatPromptTemplate.from_messages(
        [("system", PROFILE_SYSTEM_PROMPT), ("human", PROFILE_USER_PROMPT)]
    )
