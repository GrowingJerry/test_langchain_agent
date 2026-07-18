# SQLite 迁移

`infrastructure/db/connection.py` 负责 row factory、外键、busy timeout、事务、关闭连接和异常转换。`infrastructure/db/migrations.py` 负责空库初始化、`schema_versions`、增量字段和索引。

迁移原则：

- 幂等执行；
- 不删除旧表或字段；
- 不重建空数据库掩盖问题；
- 不修改数据库文件位置；
- 升级前备份；
- 在临时副本验证数据数量。

`ProjectManager` 暂时保留为 deprecated façade，公开接口委托各实体 Repository。旧 UI 和服务全部迁移完成前不删除 façade。

必要索引覆盖 `project_id`、`document_id`、`requirement_id`、`scenario_id`、`generation_run_id`、`case_id` 和 `chunk_id`。测试覆盖空库、旧库升级、重复迁移、回滚、外键、多项目隔离、CRUD 和数据数量保持。
