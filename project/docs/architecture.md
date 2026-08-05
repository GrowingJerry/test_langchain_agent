# 架构

主调用链为：`app.py → ApplicationContainer → application service → workflow/agent/chain → repository → SQLite、检索或 Ollama`。

- `ui`：Streamlit 输入和展示。
- `application`：依赖装配与业务用例编排。
- `domain`：唯一业务模型、异常和确定性规则。
- `infrastructure`：数据库、仓储、检索、LLM、文档、装备和导出适配器。
- `workflows/learning`：文档任务、知识抽取、冲突检测、审批和反馈学习。
- `workflows/scenario`：意图解析、装备分配、数量求解、编译和验证。
- `agents/test_case`：可调用检索工具的测试用例 Agent；Ollama 不可用时由应用服务切换规则 fallback。
- `chains`：仅承载固定结构化抽取或审核。

文档学习流程：上传校验 → 解析/OCR → 切块与索引 → 知识抽取 → 冲突检测 → 人工审批。场景流程：意图解析 → 项目知识与装备检索 → 分配与数量求解 → 场景编译 → 验证与审批。用例流程：构建项目上下文 → Agent 或规则生成 → 质量审核 → 保存来源与追溯 → 人工审核与导出。
# 结构化需求解析与生成链路

需求抽取采用“结构化解析优先、句子抽取兜底”。`docx` 会解析为有序
`DocumentBlock`，保留段落、标题级别、章节路径、表格行、表头、来源文档和相邻块关系；
`.doc` 会尝试临时转换为 `docx`，失败时降级为文本抽取并在质量报告中提示。

抽取结果不会直接入库。应用层先生成需求清单和质量摘要，用户在审核表中确认、修改或删除后，
才写入 `project_requirements`。写入时保留机器抽取快照和人工修改记录。

测试类型由统一注册表 `domain.rules.test_types` 维护。需求解析会给出推荐主类型、候选类型、
置信度和依据；生成页只把推荐值作为默认值，最终传入 Agent 或规则 fallback 的 `case_type`
始终是用户最终选择值。
