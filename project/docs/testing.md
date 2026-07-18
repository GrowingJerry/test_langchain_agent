# 测试与质量门禁

默认测试不要求 Ollama 在线：

```powershell
pytest -m "not ollama"
```

可选本地集成测试：

```powershell
$env:RUN_OLLAMA_TESTS="1"
pytest -m ollama
```

最终门禁：

```powershell
python scripts/check_environment.py
python -m compileall -q .
pytest -m "not ollama"
python scripts/smoke_test.py
python scripts/validate_scenario_agent.py
python -m pip check
python -m ruff check .
```

测试覆盖领域 schema、模型工厂、Ollama health、抽取 Chain、项目检索、Agent 工具与调用限制、GenerationService fallback、数据库迁移、上传安全、UI 幂等状态、structured review、Excel/Word 导出及需求—用例—来源闭环。数据库测试只能使用临时目录。

人工脚本保留为 smoke 入口，其核心断言同时由 pytest 的 repository、retriever、generation 和导出测试覆盖。不得为通过门禁屏蔽测试、删除断言或扩大异常捕获。
