# 数据与数据库

运行数据位于 `project/outputs/`。默认数据库为
`outputs/sqlite/project_workspace.db` 与
`outputs/sqlite/case_library.db`；每个项目的上传资料和导出文件分别位于
`outputs/projects/<project_id>/uploads/` 与
`outputs/projects/<project_id>/exports/`。自动评测报告位于
`outputs/evaluation/`。这些路径保持不变并由 `.gitignore` 排除。

清理或迁移前应在本地复制数据库；备份文件不得提交。迁移由 `infrastructure/database/migrations.py` 以追加和非破坏方式执行。所有项目业务表必须以 `project_id` 过滤，禁止跨项目隐式读取；全局装备也必须显式选择。
# 需求抽取与生成元数据

`project_requirements` 保留旧字段，同时追加结构化需求字段、推荐测试类型字段、
`machine_extraction_json`、`review_changes_json` 和 `retained`。这些列用于兼容旧数据、
保存机器抽取快照和人工审核修改记录。

`generation_runs.metadata_json` 保存本次生成的推荐测试类型、用户最终选择类型、是否人工覆盖、
推荐置信度、推荐依据和用例数量。`generated_cases.case_json.provenance` 也会保存同一份关键信息，
用于后续追溯和反馈学习。
