# 兼容与废弃模块

## 已删除

- `ui/project_pages.py`：无导航、测试或文档入口；六页面功能已迁移到 service-backed UI。
- `core/test_case_reviewer.py`：无调用；已由 `ReviewService` 和 `TestCaseReviewChain` 替代。
- `core/textgen_pb2.py`、`core/textgen_pb2_grpc.py`：无调用；当前 Ollama 使用 HTTP/ChatOllama。

## 保留的兼容层

- `core/advanced_case_generator.py`：验证脚本和兼容测试仍使用；委托 `GenerationService`。
- `core/project_case_generator.py`：兼容测试仍使用；委托 `GenerationService`。
- `core/test_case_generator.py`：保留绑定项目的旧类名；未绑定项目的生成已禁止。
- `core/project_manager.py`：UI/服务仍依赖公开 façade；内部委托 repositories。
- `core/project_kb.py`：上下文和上传流程仍依赖；内部委托项目绑定 retriever。
- `core/ollama_client.py`：六性兼容类型及视觉精确 JSON 解码仍引用；不再负责聊天生成、JSON 截取或修复。
- `rag/vector_search.py`：无运行时调用，但占位 API 的外部兼容性无法确认，保留并发出 DeprecationWarning。
- `core/visual_client.py`：视觉证据流程仍在使用，不能删除。
- `ollama/`：本地容器部署资产仍有效，不能删除。

重复或同义 schema 仅在兼容层保留：`ReviewResult`、`RequirementItem`、`ScenarioItem` 等旧名称通过兼容导出或适配器使用；新代码应使用领域规范字段。删除前必须再次验证全仓运行时、测试和 README 引用。
