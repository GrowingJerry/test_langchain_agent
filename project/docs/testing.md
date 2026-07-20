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
