# 架构

## 新旧对照

| 旧结构 | 当前结构 |
|---|---|
| UI 直接调用 manager、模型和旧生成器 | UI 仅调用 `UIApplicationService` |
| `core/` 同时负责提示词、模型、解析和持久化 | `agents/`、`chains/`、`services/`、`infrastructure/`、`domain/` 分层 |
| 手工截取模型 JSON | Pydantic 与 LangChain structured output |
| `ProjectManager` 数据库大类 | Repository 持久化，`ProjectManager` 为兼容 façade |
| 多个生成器各自生成和保存 | `GenerationService` 统一生成、评分、保存和追溯 |

## 依赖方向

```text
Streamlit UI
  -> services
     -> agents / chains / domain
     -> infrastructure.retrieval / infrastructure.llm
     -> infrastructure.db.repositories
        -> SQLite
```

UI 不执行 SQL、不创建 Agent、不调用模型、不拼提示词。Repository 不调用 LLM，不做业务决策。领域 schema 不依赖 Streamlit 或数据库。

## 项目隔离

文档、chunk、画像、需求、场景、用例、审查与追溯查询都要求 `project_id`。Agent 工具从 `AgentRuntimeContext` 获取绑定项目，模型不能传入任意项目 ID。Repository 查询再次使用项目条件过滤。

## 生成闭环

```text
GenerationRequest
 -> 校验 project/requirement/scenario
 -> 构建项目上下文
 -> Agent 或规则 fallback
 -> Pydantic 校验
 -> 确定性质量评价
 -> generation run / cases / trace sources
 -> GenerationResult
```
