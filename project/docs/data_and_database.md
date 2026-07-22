# 数据与数据库

运行数据位于 `project/outputs/`。默认数据库为
`outputs/sqlite/project_workspace.db` 与
`outputs/sqlite/case_library.db`；每个项目的上传资料和导出文件分别位于
`outputs/projects/<project_id>/uploads/` 与
`outputs/projects/<project_id>/exports/`。自动评测报告位于
`outputs/evaluation/`。这些路径保持不变并由 `.gitignore` 排除。

清理或迁移前应在本地复制数据库；备份文件不得提交。迁移由 `infrastructure/database/migrations.py` 以追加和非破坏方式执行。所有项目业务表必须以 `project_id` 过滤，禁止跨项目隐式读取；全局装备也必须显式选择。
