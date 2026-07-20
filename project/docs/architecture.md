# 系统架构

## 分层与依赖

```text
Streamlit UI
  -> UIApplicationService / 领域 Service
     -> 场景工作流 / 学习流程 / TestCase Agent / Chain
        -> Retrieval + Repository
           -> SQLite
```

- `ui/` 只收集输入和展示结构化结果，不包含 SQL、模型调用或持久化规则。
- `services/` 提供 UI 应用边界并保留旧入口适配。
- `scenario_engine/` 执行确定性场景编译、装备分配、数量求解和校验。
- `learning/` 处理后台文档作业、领域学习、知识冲突审核和反馈候选。
- `equipment/` 处理 JSONL 标准化、检索、能力匹配和配置规则。
- `agents/test_case/` 是唯一开放式 Agent；所有工具闭包绑定当前 `project_id`。
- `chains/` 只承担固定输入输出的结构化模型任务。
- `infrastructure/db/repositories/` 集中传统实体持久化；新增领域流程中的 SQL 位于 Service/Repository 层，绝不进入 UI。
- `domain/schemas/` 使用 Pydantic v2 严格模型。

## 兼容边界

旧 `ScenarioItem`、`ScenarioCard` 和 `models.schemas` 继续服务 Excel、旧场景抽取和导出入口；新 `ScenarioIntent`、`ScenarioSpec` 服务场景编译，两者不是重复模型。`ProjectManager` 保留为兼容 facade，将旧公开方法委托给 Repository。`rag/scenario_generator.expand_scenario()` 已接入新工作流并返回旧扁平格式。

## 数据隔离与来源

文档、chunk、学习任务、知识、装备、库存、场景、校验、反馈和生成结果均带 `project_id`。Repository 和 Service 查询必须传入当前项目；`GLOBAL` 知识、模板或装备只有显式允许时才合并。事实来源可追踪到 `document_id`、`chunk_id`、页码或 JSONL 行号。

## 长任务

长文档由 SQLite 作业队列驱动：上传创建作业，Worker 原子领取租约，解析器逐页产出，按固定页数提交进度。中断后从已提交页恢复，单页失败不终止整本资料。
