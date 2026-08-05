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

## 需求解析与生成说明

项目资料上传支持 `txt`、`md`、`doc`、`docx`、`pdf`。正式需求文档建议使用
`docx`：系统会优先保留标题层级、表格行、原始需求编号和来源位置；legacy
`.doc` 会尽量通过 Word/WPS 转为临时 `docx` 解析，无法安全转换时按文本兜底并提示用户转换。

“抽取/更新需求”会先进入人工审核预览，不会立即把模板说明当作项目事实入库。模板注释、
“本条应……”“在形成最后文档时……”以及明显占位符内容会被过滤或标记。

推荐测试类型只是默认建议。生成页面会根据需求的推荐类型默认选中测试类型，但用户最终选择优先；
用户修改后本次生成会采用人工选择值。测试用例数量始终由用户手动设置，系统不会自动强制修改。
