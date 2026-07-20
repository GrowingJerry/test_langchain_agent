# SQLite 迁移、备份与升级

`infrastructure/db/migrations.py` 是唯一 schema 创建入口。`schema_versions` 记录版本；`CREATE TABLE/INDEX IF NOT EXISTS` 和 `_ensure_columns` 保证重复执行安全。迁移只新增表、列和索引，不删除或重命名既有结构，不覆盖数据。

## 备份

停止 Streamlit 和 Worker，确认没有写事务后执行：

```powershell
Copy-Item outputs\sqlite\project_workspace.db outputs\sqlite\project_workspace.backup.db
Copy-Item outputs\sqlite\case_library.db outputs\sqlite\case_library.backup.db
```

建议同时记录文件大小和哈希。不要在运行中的数据库上只复制 WAL 主文件；如启用了 WAL，应使用 SQLite backup API 或在停机后连同 `-wal/-shm` 文件一起处理。

## 升级验证

先复制为临时文件，再构造 `ProjectManager` 触发迁移：

```powershell
python -c "from pathlib import Path; from core.project_manager import ProjectManager; ProjectManager(Path('outputs/sqlite/project_workspace.backup.db')); print('upgrade ok')"
pytest -q tests/unit/test_compilation_migrations.py tests/unit/test_database_repositories.py
```

核对 `schema_versions`、表数量、关键业务记录数量和外键。失败时保留原库，修复迁移根因后重试，不通过删库规避。

## 新领域表

学习与文档：`learning_tasks`、`document_processing_jobs`、`document_sections`、`knowledge_units`、`knowledge_relations`、`knowledge_conflicts`、`knowledge_reviews`。

装备：`equipment_entities`、`equipment_aliases`、`equipment_capabilities`、`equipment_role_mappings`、`equipment_configuration_rules`、`project_equipment_inventory`、`equipment_import_errors`。

场景：`scenario_templates`、`scenario_generation_runs`、`scenario_equipment_allocations`、`scenario_validation_results`。

反馈：`feedback_candidates`、`approved_learning_rules`、`feedback_corrections`、`feedback_rule_audit`。

JSON 列统一为 TEXT，并通过 `infrastructure/db/json_codec.py` 编解码。
