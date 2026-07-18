# 测试用例 Agent 设计

## 为什么只有用例生成使用 Agent

测试用例生成需要根据需求、场景、来源文档、测试方法和历史写作参考动态决定检索顺序与工具选择，适合使用 Agent。画像抽取、场景抽取和审查的步骤固定、输出固定，应使用 Chain，避免开放式 Agent 带来的不确定调用和额外风险。

## LangChain v1

Agent 使用 `langchain.agents.create_agent`、`ChatOllama` 和 Pydantic structured output。不使用 `AgentExecutor`、`initialize_agent`、`create_react_agent` 或自定义 ReAct 循环。

## 工具

- `get_project_profile`
- `get_requirement_context`
- `search_project_documents`
- `get_related_scenarios`
- `get_test_method_guidance`
- `get_reference_cases`

工具只读。历史用例工具明确标记为“仅作方法和格式参考”。文档检索返回 chunk ID、文档名及位置元数据。

## 项目作用域隔离

`AgentRuntimeContext` 在创建时绑定 `project_id`。工具参数不包含任意 `project_id`，所有查询通过闭包和项目绑定 retriever 执行；返回前再次校验结果项目 ID。

## Structured output

Agent 输出为 `GeneratedCaseBundle`，包含 `cases`、`overall_missing_information`、`used_tool_names`、`retrieved_source_chunk_ids` 和 `warnings`。用例字段由领域 Pydantic schema 校验，不从自由文本中截取或修复 JSON。

## Fallback

以下情况由上层 `GenerationService` 转入规则模式：Agent 被关闭、Ollama 不可用或模型缺失、Agent/工具超限、超时、工具异常、structured output 校验失败。结果标记 `generation_mode=rule_fallback` 并记录可读 `fallback_reason`。

## 调用限制

模型调用数、工具调用数、模型重试、工具重试和 structured output 重试均由 Settings 限制。Agent 不实现无限循环；超限抛出领域异常。

## 来源追溯

当前项目文档是事实来源。每条用例关联 requirement、scenario、source chunk 和 source document；保存用例时同时保存 `trace_sources`。历史用例 ID 不作为当前项目事实来源。

## 人工审核边界

缺失接口、阈值、状态、设备、参数或环境时设置 `need_human_confirm` 和 `missing_information`。规则审查、六性质量评价和 structured LLM review 只返回状态与建议，不自动修改原用例；修改必须由用户显式确认。
