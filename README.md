# test_langchain_agent

基于 Python 3.10、Streamlit、SQLite、LangChain 与本地 Ollama 的项目级测试用例系统。支持文档上传与长文档任务、知识抽取和审批、装备 JSONL、场景编译、测试用例 Agent、规则 fallback、审核追溯、导出和自动评测；所有业务数据按 `project_id` 隔离。

## 安装与启动

```powershell
conda activate test_agent
cd project
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
streamlit run app.py
```

Docling 与 OCR 分别使用 `requirements-docs.txt` 和 `requirements-ocr.txt`。项目固定使用 Python 3.10，不使用 `pyproject.toml`。

最短流程：创建项目 → 上传文档 → 抽取并审批知识 → 导入装备 → 编译并审批场景 → 生成用例 → 审核追溯 → 导出。Ollama 不可用时自动转为确定性规则生成。

## 目录

- `project/application`：应用服务和依赖装配
- `project/domain`：唯一领域模型与规则
- `project/infrastructure`：数据库、仓储、检索、LLM、文档、装备和导出
- `project/workflows`：学习与场景工作流
- `project/agents/test_case`：测试用例 Agent
- `project/ui`：Streamlit 页面
- `project/tests`：单元、集成、评测和 golden 基准

## 验证

```powershell
cd project
python -m compileall -q .
pytest -m "not ollama"
python scripts/smoke_test.py
python scripts/validate_scenario_agent.py
python scripts/evaluate_scenario_pipeline.py
python -m pip check
python -m ruff check .
```

评测报告写入被忽略的 `project/outputs/evaluation/`。

## 文档

- [架构](project/docs/architecture.md)
- [用户指南](project/docs/user_guide.md)
- [开发指南](project/docs/development_guide.md)
- [数据与数据库](project/docs/data_and_database.md)
- [测试](project/docs/testing.md)
