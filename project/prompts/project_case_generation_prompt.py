# -*- coding: utf-8 -*-
"""Prompt template for project-knowledge-based test case generation."""


def build_project_case_prompt(
    project_profile: str,
    requirement: str,
    case_type: str,
    project_chunks: str,
    similar_cases: str,
    six_quality: str,
    test_methods: str,
) -> str:
    """Build a strict JSON prompt grounded in current project documents."""
    return f"""你是项目级测试文档生成系统，请基于“当前项目资料”生成一条测试用例。

必须遵守：
1. 只能依据当前项目画像、当前需求点、当前项目文档片段生成，不得混入其他项目内容。
2. 不得编造当前项目文档中没有的性能指标、阈值、接口字段、设备型号或环境参数。
3. 如果缺少性能阈值或判定阈值，必须写“需人工确认”或“按需求文档规定值判定”。
4. 历史相似测试用例只能作为格式、步骤组织和测试方法参考，不能覆盖当前项目文档。
5. 必须保留来源字段：source_document、source_chunk_ids、requirement_id。
6. 只输出合法 JSON 对象，不要输出 Markdown 代码块，不要输出额外说明。

当前项目画像：
{project_profile}

当前需求点：
{requirement}

生成类型：
{case_type}

现有六性分类：
{six_quality}

推荐测试方法：
{test_methods}

当前项目文档片段：
{project_chunks}

历史相似测试用例（仅作格式和方法参考）：
{similar_cases}

请输出 JSON 字段：
case_id
case_name
case_type
requirement_id
test_purpose
prerequisites
test_steps
expected_results
pass_criteria
source_document
source_chunk_ids
need_human_confirmation
notes
"""
