# -*- coding: utf-8 -*-
"""测试用例合规审查提示词（可选 Ollama）。"""


def build_review_prompt(case_json_text: str) -> str:
    """构建审查用提示词。"""
    return f"""你是测试用例质量审查专家。请审查以下 JSON 表示的单条测试用例。

请输出严格 JSON 对象，字段如下：
{{
  "review_issues": ["问题1", "问题2"],
  "review_suggestions": ["建议1", "建议2"]
}}

不要编造需求中未出现的指标问题；关注可执行性、可判定性、敏感内容规避。

待审查用例 JSON：
{case_json_text}
"""
