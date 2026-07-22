# 开发指南

- UI 仅收集输入、调用应用服务并展示结果。
- 编排代码放在 `application/services`；确定性流程放在 `workflows`。
- 领域模型和规则放在 `domain`，不得依赖 UI 或基础设施。
- SQLite、仓储、文档、检索、LLM、装备和导出适配器放在 `infrastructure`。
- 测试用例 Agent 及其提示词放在 `agents/test_case`；固定结构化抽取保留在 `chains`。

新增能力时先扩充唯一的领域模型或应用服务，再添加单元测试；涉及数据库、项目隔离或完整流程时增加集成测试。禁止新增旧路径包装器或第二份实现。
