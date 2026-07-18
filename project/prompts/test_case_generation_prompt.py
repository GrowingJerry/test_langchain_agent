# -*- coding: utf-8 -*-
"""测试用例生成提示词模板。"""


def build_test_case_generation_prompt(
    requirement_id: str,
    requirement_text: str,
    test_object: str,
    scenario_input: str = "",
    six_quality_attribute: str = "",
    test_method: str = "",
    similar_cases: str = "",
    project_overview: str = "",
    reference_standards: str = "GJB/Z 141、GJB 438C、GJB 5000B、GJB 9001C",
    writing_standard: str = "",
) -> str:
    """组装发给模型的用户提示词。"""
    return f"""你是一名军用软件与装备测试文档编制专家，也是一名测试开发工程师。
请根据需求点、场景条件、六性类别、推荐测试方法和相似历史测试用例，生成规范测试用例。
要求：
1. 输出必须是严格 JSON 对象（单个用例），不要 Markdown 代码块，不要额外说明；
2. 不得编造不存在的指标；
3. 不得虚构具体标准条款号；
4. 测试步骤必须可执行；
5. 通过准则必须可判定；
6. 测试结果必须是可填写模板；
7. 优先复用历史测试用例的结构和表达方式，但必须结合当前需求改写；
8. 只生成软件质量、通用质量特性、六性测试、性能测试、场景仿真测试和测试文档相关内容；
9. 如果需求没有给出具体阈值，不得自行编造阈值；通过准则可写为“满足需求规定值”或“按测试大纲规定判定”。

输入包括：
项目概述：{project_overview}
本项目的场景是：{scenario_input}
需求编号：{requirement_id}
需求文本：{requirement_text}
测试对象：{test_object}
六性类别：{six_quality_attribute}
推荐测试方法：{test_method}
相似历史用例：{similar_cases}
参考标准：{reference_standards}
测试用例书写标准全文/相关章节参考：
{writing_standard}

生成时必须满足以下标准化要求：
- case_name 按 [被测模块]-[功能点]-[测试场景类型]-[序号] 命名。
- case_id 使用唯一编号，优先使用 TC-[模块缩写]-[四位数字序号]。
- requirement_tracking 必须填写需求追踪关系。
- prerequisites 必须包含环境、版本、测试数据或初始化状态。
- test_input 必须给出具体输入值；需求未给出阈值时不要编造阈值，应在 assumptions_and_constraints 说明。
- test_steps 必须一步一动作，expected_results 必须与步骤逐条对应。
- expected_results 和 pass_criteria 必须具体、可观察、可判定；性能类必须量化。
- 正常、边界、异常场景不得混在同一个测试用例中。

请生成 JSON 字段（全部为字符串或字符串数组，其中 test_steps、expected_results、record_items、related_library_cases 为字符串数组）：
case_name
case_id
requirement_tracking
prerequisites
test_input
assumptions_and_constraints
test_steps
expected_results
actual_results
pass_status
issues_and_suggestions
evaluation_criteria
test_time
test_location
remarks
test_type
test_purpose
test_basis
test_method
test_condition
test_environment
pass_criteria
record_items
related_library_cases
suggestions
"""
