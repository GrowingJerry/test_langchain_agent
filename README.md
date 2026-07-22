# 项目级测试文档智能生成系统

系统基于 Python 3.10、Streamlit、SQLite、Ollama 和 LangChain v1，提供项目文档入库、长文档学习、装备知识库、确定性场景编译、测试用例 Agent、审核追溯和本地评测。项目正式资料和 approved 知识是事实依据；书籍、历史用例和候选反馈不能覆盖当前项目已批准事实。

## 安装与启动

```powershell
cd project
conda activate test_agent
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
streamlit run app.py
```

可选的 Docling 文档增强依赖单独位于 `requirements-docs.txt`；扫描 PDF 的本地 RapidOCR 依赖位于 `requirements-ocr.txt`。没有安装这些可选依赖时默认 PyMuPDF 仍可工作，并将扫描页标记为 `needs_ocr`。本项目不使用或建议使用 `pyproject.toml`。

## 主要用户流程

1. 在“项目工作台”创建项目。
2. 在“文档与知识库/项目文档”上传普通需求或技术书籍。大文件自动进入后台作业。
3. 在“装备数据”上传 JSONL，选择当前项目或显式选择 `GLOBAL`。
4. 在“场景学习”创建学习任务，选择文档和学习范围，抽取结构化知识。
5. 在“知识审核”处理冲突并批准可用于生成的知识。
6. 在“智能生成”输入最小场景意图，审核场景草稿、装备配置和阻断问题，再生成多条测试用例。
7. 人工修改只形成反馈候选；只有人工批准的反馈规则才参与后续生成。

## 装备导入

导入器逐行读取真实 JSONL，保留原始载荷、文件、行号和稳定哈希：

```powershell
python scripts/import_military_jsonl.py --file data/military.jsonl --project-id GLOBAL
```

`GLOBAL` 是组织级通用装备库。任何查询必须显式设置允许访问 GLOBAL，当前项目数据始终优先。详见 `project/docs/equipment_knowledge_base.md`。

## 长文档与 Worker

扫描 PDF 建议先安装本地 CPU OCR：

```powershell
python -m pip install -r requirements-ocr.txt
```

RapidOCR 仅处理 PyMuPDF 判断为低文本的页面，默认 220 DPI；OCR 文本、页码、平均置信度和引擎信息会随片段保存。

普通小文件同步处理；达到页数或字节阈值的大文件创建 `document_processing_jobs`。启动本地 SQLite Worker：

```powershell
python scripts/run_learning_worker.py
```

作业支持暂停、恢复、取消、失败重试和租约防重复领取。扫描页第一版只标记 `needs_ocr=true`。详见 `project/docs/project_learning.md`。

## 数据库备份与升级

升级前停止 Streamlit 和 Worker，然后复制数据库：

```powershell
Copy-Item project\outputs\sqlite\project_workspace.db project\outputs\sqlite\project_workspace.backup.db
Copy-Item project\outputs\sqlite\case_library.db project\outputs\sqlite\case_library.backup.db
```

应用启动或构造 `ProjectManager` 时自动执行幂等、增量、非破坏迁移。迁移只新增表、字段和索引，不删除既有数据。应先在备份副本验证，详见 `project/docs/database_migrations.md`。

## 测试与本地评测

```powershell
python scripts/check_environment.py
python -m compileall -q .
pytest -m "not ollama"
python scripts/smoke_test.py
python scripts/validate_scenario_agent.py
python scripts/evaluate_scenario_pipeline.py
python -m pip check
python -m ruff check .
```

评测使用 `tests/golden/*.jsonl`，输出 `evaluation_report.json` 和 `evaluation_report.md`。硬门槛包括跨项目泄漏率、无来源数量填充率、书籍覆盖项目参数率均为 0，来源引用有效率为 100%。

## 文档

- `project/docs/architecture.md`
- `project/docs/agent_design.md`
- `project/docs/database_migrations.md`
- `project/docs/testing.md`
- `project/docs/project_learning.md`
- `project/docs/scenario_compiler.md`
- `project/docs/equipment_knowledge_base.md`
