# 测试用例 Agent 设计

## 职责边界

场景编译不是自由规划 Agent，而是确定性工作流加可选结构化 Chain。TestCase Agent 只能把已编译场景、批准知识和项目资料组织为用例，不能决定装备是否适用、不能计算装备数量、不能用书籍示例参数覆盖项目参数。

## 只读工具

基础工具读取画像、需求、项目文档、关联场景、测试方法和历史写作参考。领域工具读取 approved 知识、仿真模型、状态转换、已验证参数、approved 模板、装备候选、场景装备分配、场景校验和 approved 反馈规则。工具均由 `AgentRuntimeContext` 闭包绑定项目；GLOBAL 数据要求显式开关。

## 来源清洗

模型声明不直接成为 provenance。运行时记录实际调用工具及实际返回的 chunk、知识、装备、配置规则、场景和校验运行 ID，最终只保留这些交集。候选装备不能表述为批准配置；批准配置必须来自已编译场景分配结果。

## 调用限制与降级

模型调用、工具调用、模型重试、工具重试和 structured output 重试均由 Settings 限制。Agent、Ollama、工具或结构化输出失败时，`GenerationService` 使用 `rule_fallback` 并记录 `fallback_reason`。不会把降级伪装成 Agent 成功。

## 输出约束

`GeneratedCaseBundle` 使用 Pydantic v2，包含用例、缺失信息、实际工具、chunk、知识、装备、配置规则及场景校验运行。测试步骤和预期结果必须一一对应；缺失事实时设置 `need_human_confirm`。
