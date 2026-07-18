# 项目级测试文档智能生成系统

本项目使用 Streamlit、SQLite、Ollama 和 LangChain v1，按项目管理文档、画像、需求、场景、测试用例、审查、来源追溯及 Excel/Word 导出。当前项目文档是事实依据；历史用例只提供测试方法和写作格式参考。

## 运行要求

- Python 固定为 3.10。
- 依赖由 `requirements.txt` 和 `requirements-dev.txt` 管理。
- 本项目不使用也不创建 `pyproject.toml`，不使用 Poetry、PDM 或 uv。
- Ollama 在本地运行，云端追踪默认关闭。

Windows 安装命令：

```powershell
cd project
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

也可使用已清理的 Conda 环境：

```powershell
conda activate test_agent
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

## Ollama

启动服务并准备模型：

```powershell
ollama serve
ollama pull qwen3:8b
ollama pull nomic-embed-text
ollama list
python scripts/check_environment.py
```

模型名称和地址由 `.env` 与 `config/settings.py` 统一管理。`ollama/` 目录保留可选的本地容器部署资产。

## 普通模式与 Agent 模式

- 普通模式：设置 `ENABLE_AGENT=false`，测试用例走确定性规则生成。
- Agent 模式：设置 `ENABLE_AGENT=true` 且 `ENABLE_OLLAMA=true`，测试用例由 LangChain v1 `create_agent` 生成。
- Ollama、工具、模型或 structured output 失败时，服务进入 `rule_fallback`，并保存 `fallback_reason`，不会伪装为 Agent 成功。
- 画像、场景和审查是固定结构任务，使用 Chain；只有测试用例生成使用 Agent。

启动应用：

```powershell
streamlit run app.py
```

六个页面保持为：项目工作台、文档与知识库、智能生成、结果审查与追溯、导出中心、系统设置。

## 数据与备份

默认数据库：

```text
outputs/sqlite/project_workspace.db
outputs/sqlite/case_library.db
```

升级前先停止 Streamlit，再备份数据库：

```powershell
Copy-Item outputs\sqlite\project_workspace.db outputs\sqlite\project_workspace.backup.db
Copy-Item outputs\sqlite\case_library.db outputs\sqlite\case_library.backup.db
```

启动应用或构造 `ProjectManager` 时会自动运行幂等、增量迁移。迁移只建表、增加缺失字段和索引，不删除既有表、字段或数据。可先在数据库副本上执行：

```powershell
python -c "from pathlib import Path; from core.project_manager import ProjectManager; ProjectManager(Path('outputs/sqlite/project_workspace.backup.db')); print('upgrade ok')"
```

详见 `docs/database_migrations.md`。

## 测试

```powershell
python scripts/check_environment.py
python -m compileall -q .
pytest -m "not ollama"
python scripts/smoke_test.py
python scripts/validate_scenario_agent.py
python -m pip check
python -m ruff check .
```

可选的本地 Ollama 集成测试默认不运行：

```powershell
$env:RUN_OLLAMA_TESTS="1"
pytest -m ollama
```

所有自动化数据库测试使用临时目录，不应指向正式 `outputs/sqlite` 数据库。更多信息见 `docs/testing.md`。

## 安全和人工审核

上传文件使用扩展名、大小、路径和解析资源限制。生成内容不得编造接口、阈值、状态、设备或环境；资料不足时必须设置 `need_human_confirm=true` 并填写 `missing_information`。审查只产生状态和建议，只有用户显式确认后才更新原用例。

架构与兼容信息：

- `docs/architecture.md`
- `docs/agent_design.md`
- `docs/database_migrations.md`
- `docs/testing.md`
- `docs/deprecation.md`
