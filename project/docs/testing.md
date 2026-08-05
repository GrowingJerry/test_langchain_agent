# 测试与本地评测

## 完整质量门禁

在 `conda activate test_agent` 后运行：

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

Ollama 测试用 marker 隔离，只有本地模型准备完成时运行：

```powershell
$env:RUN_OLLAMA_TESTS="1"
pytest -m ollama
```

## Golden 评测

固定数据位于 `tests/golden/scenario_cases.jsonl` 和 `equipment_allocations.jsonl`。运行：

```powershell
python scripts/evaluate_scenario_pipeline.py
```

脚本输出 JSON 和 Markdown 报告。指标包括场景字段完整率、来源覆盖、不支持事实、装备角色覆盖、数量追踪、参数单位条件、跨项目泄漏、阻断识别、书籍覆盖、步骤对应、重复运行稳定性和人工修改比例。

硬门槛不可弱化：跨项目泄漏率、无来源数量填充率、approved 项目事实被书籍覆盖率必须为 0；来源引用有效率必须为 100%。

## 测试数据纪律

数据库测试只能使用 pytest 临时目录。不得指向正式 `outputs/sqlite`；不得为通过测试删除断言、扩大异常捕获或跳过失败用例。
# 需求解析验收测试

需求解析相关测试必须覆盖结构化块、表格行、原始编号保留、模板过滤、测试类型推荐、
人工覆盖状态和生成追溯。不要只断言“数量大于 0”；应检查具体需求编号、章节路径、
结构化字段、推荐类型和来源证据。

推荐运行：

```powershell
cd project
python -m compileall -q .
pytest tests/unit/test_structured_requirement_extraction.py `
       tests/unit/test_test_type_recommendation.py `
       tests/integration/test_requirement_generation_e2e_acceptance.py `
       tests/integration/test_generation_service.py
```

端到端验收应覆盖：上传或登记样本文档、结构化抽取、质量报告、人工审核保存、推荐测试类型默认值、
人工修改不被 rerun 覆盖、按人工最终类型生成用例、保存追溯并导出需求-用例矩阵。
