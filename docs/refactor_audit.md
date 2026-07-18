# `project/` 重构审计与基线报告

审计日期：2026-07-18  
审计范围：`AGENTS.md`、`project/README.md`、`project/app.py`、`project/config/settings.py`、`project/core/`、`project/models/`、`project/rag/`、`project/ui/`、`project/scripts/`、`project/requirements.txt`，以及上述模块实际引用的 `project/prompts/`。  
本次变更边界：仅新增本文档；未修改业务实现、依赖声明或正式数据库。

## 1. 执行摘要

当前系统的主运行链路已经具备多项目隔离、文档入库、混合检索、画像/需求/场景抽取、场景化用例生成、规则 fallback、审查评分、来源追溯和 Excel/Word 导出能力，但“Agent”目前只是固定编排类，并非 LangChain Agent。

主要重构动因如下：

1. Ollama 文本、视觉和 embedding 分成三套直接 HTTP 客户端，结构化输出依靠手工 JSON 提取。
2. `AdvancedCaseGenerator`、`ProjectCaseGenerator`、`TestCaseGenerator` 三套生成器并存，数据模型和输出字段不统一。
3. `ProjectManager` 同时承担数据库初始化、兼容迁移、目录管理及几乎全部领域对象的 CRUD。
4. 主 UI 直接协调存储、模型客户端、提示词和业务服务，尤其审查页面直接拼装提示词。
5. Pydantic 模型主要覆盖旧用例结构，主场景化生成链路仍大量传递无类型约束的 `Dict[str, Any]`。
6. 现有基线偏向离线 fallback 和 happy path，未覆盖真实 Ollama、错误路径、数据库迁移、旧生成器和跨项目恶意输入。

## 2. 当前目录职责图

```text
project/
├─ app.py                    Streamlit 组合根；创建 ProjectManager/OllamaClient/历史库
├─ config/settings.py        环境变量、模型名、输出路径；导入时创建输出目录
├─ ui/                       页面展示，同时承担部分应用编排
│  ├─ navigation.py          当前项目选择
│  ├─ workbench_page.py      项目概况与流程状态
│  ├─ knowledge_page.py      文档/知识库/视觉资产；直接调用视觉模型和提示词
│  ├─ generation_page.py     画像、需求、场景及 AdvancedCaseGenerator 编排
│  ├─ review_trace_page.py   规则审查、直接 Ollama 审查、人工修改、追溯
│  ├─ export_page.py         项目导出编排
│  ├─ settings_page.py       运行配置 UI
│  └─ project_pages.py       旧页面集合；仍引用 ProjectCaseGenerator
├─ core/
│  ├─ project_manager.py     SQLite schema/迁移/项目目录/全部项目领域 CRUD
│  ├─ document_*.py          文档解析、切分、入库
│  ├─ project_kb.py          当前项目 keyword + embedding 混合检索
│  ├─ embedding_client.py    Ollama embedding HTTP 客户端
│  ├─ context_builder.py     场景化生成上下文组装
│  ├─ *_extractor.py         画像、规则需求、场景卡抽取
│  ├─ *_generator.py         三套用例生成实现及导出/矩阵辅助
│  ├─ test_case_reviewer.py  规则与 Ollama 审查
│  ├─ case_quality_evaluator.py  确定性质量评分
│  ├─ case_library.py        独立历史用例 SQLite、导入和相似检索
│  ├─ ollama_client.py       文本 Ollama HTTP + 手工 JSON 提取
│  ├─ visual_client.py       视觉 Ollama HTTP + 复用手工 JSON 提取
│  └─ textgen_pb2*.py        未接入的生成式 gRPC 代码
├─ models/                   Pydantic 旧需求/场景/用例/审查/视觉模型
├─ prompts/                  各生成器、审查和视觉抽取提示词构造
├─ rag/
│  ├─ vector_search.py       未接入的空向量检索占位
│  ├─ scenario_generator.py  场景数据读取/展示辅助
│  ├─ report_generator.py    报告数据组装
│  ├─ docx_renderer.py       DOCX 渲染
│  └─ standard_kb/           标准知识库包占位/入口
└─ scripts/                  smoke、场景闭环、稳定性检查和样例资产脚本
```

## 3. 当前生成流程调用图

### 3.1 主六页面场景化生成链路

```text
app.main
  ├─ ProjectManager(project_db)
  ├─ OllamaClient(ollama_url, model) [可关闭]
  └─ render_generation_page
       ├─ 画像：manager.combined_project_text
       │        -> extract_project_profile
       │        -> OllamaClient.generate_json 或规则 fallback
       │        -> manager.save_profile
       ├─ 需求：manager.list_chunks
       │        -> extract_requirements_from_chunks [规则]
       │        -> manager.replace_requirements
       ├─ 场景：extract_and_save_scenario_cards
       │        -> OllamaClient.generate_json 或规则 fallback
       │        -> manager.replace_full_scenario_cards
       └─ 用例：AdvancedCaseGenerator
                -> ContextBuilder.build
                   ├─ manager 读取画像/需求/场景/视觉证据
                   ├─ search_project_chunks(project_id)
                   ├─ 六性分类 + 测试方法匹配
                   └─ 历史用例检索 [仅写法/方法参考]
                -> build_advanced_case_prompt
                -> OllamaClient.generate_json 或 _fallback_case
                -> 来源 ID 白名单过滤、缺失信息合并
                -> evaluate_case_quality
                -> manager.save_generated_case
                -> manager.save_quality_score
                -> manager.save_review_result
```

该链路名称含“Agent”，实际没有工具调用、状态图或自主决策循环；它是固定顺序的应用服务。

### 3.2 旧项目用例链路

```text
ui/project_pages.py
  -> ProjectCaseGenerator.generate
     -> search_project_chunks
     -> 六性分类 + 测试方法匹配
     -> build_project_case_prompt
     -> OllamaClient.generate_json 或 _fallback_case
     -> 调用方 manager.save_generated_case
```

`project_pages.py` 已不在 `app.py` 六页面导航中，但仍是仓库内有效可导入兼容代码，不能在未确认外部调用前删除。

### 3.3 旧通用用例链路

```text
TestCaseGenerator.generate_cases
  -> 六性分类/测试方法匹配
  -> CaseLibraryManager.search_similar_cases
  -> standard_reference_for_prompt
  -> build_test_case_generation_prompt
  -> OllamaClient.generate_json
  -> _dict_to_test_case 或规则生成
```

仓库内没有发现 `TestCaseGenerator` 的实例化调用方；它仍可能是外部/API 兼容入口，第一阶段不可据此直接删除。

## 4. Ollama 调用入口

| 入口 | API | 调用方 | fallback/行为 |
|---|---|---|---|
| `core/ollama_client.py::check_connection/list_models` | `GET /api/tags` | app 创建的文本客户端及各生成/审查流程 | 请求异常返回 `False`/空列表 |
| `core/ollama_client.py::chat` | `POST /api/chat` | `generate_json` | 异常向上传递给 `generate_json` |
| `core/ollama_client.py::generate_json` | `POST /api/chat` | 画像、场景、六性、三套生成器、审查、UI 审查 | 请求异常/解析失败返回 `None` |
| `core/embedding_client.py` | `POST /api/embeddings`，失败再试 `/api/embed` | `project_kb.py` | 两种接口均失败返回 `None`，检索退回关键词 |
| `core/visual_client.py` | `GET /api/tags`、`POST /api/chat` + images | `ui/knowledge_page.py`、`scripts/stability_check.py` | 返回带 `ok/error` 的字典；JSON 解析复用 `OllamaClient.extract_json` |

具体文本模型消费位置：

- `project_profile_extractor.py`
- `scenario_card_extractor.py`
- `six_quality_classifier.py`
- `advanced_case_generator.py`
- `project_case_generator.py`
- `test_case_generator.py`
- `test_case_reviewer.py`
- `ui/review_trace_page.py`

## 5. 手工提示词拼装位置

### UI 层

- `ui/review_trace_page.py:112`：直接拼装 Ollama 审查提示词，且示例 `{status:'...',issues:[]}` 不是合法 JSON。
- `ui/knowledge_page.py:76`：UI 直接调用 `build_visual_evidence_prompt` 并执行视觉模型。

### Core 层内联提示词

- `project_profile_extractor.py:80`：画像 JSON 字段和事实约束。
- `scenario_card_extractor.py:76`：场景卡 JSON 提示词。
- `six_quality_classifier.py`：六性分类 system/user prompt。
- `ollama_client.py:116`：追加“只输出合法 JSON”系统指令。
- `visual_client.py:189`：追加视觉 JSON 输出指令。
- `test_case_reviewer.py:128`：审查提示词再拼接标准全文。
- `test_case_generator.py`：组装历史用例、标准、测试方法和 system prompt。

### prompts 模块

- `prompts/advanced_case_generation_prompt.py`
- `prompts/project_case_generation_prompt.py`
- `prompts/test_case_generation_prompt.py`
- `prompts/review_prompt.py`
- `prompts/visual_evidence_prompt.py`

提示词已部分模块化，但画像、场景、六性和 UI 审查仍内联；UI 不应继续拥有提示词或模型执行职责。

## 6. 手工 JSON 提取、修复与存储解码

需要区分“模型文本提取”和“数据库 JSON 列解码”：

- `ollama_client.py::strip_think_tags`：正则删除 `<think>`。
- `ollama_client.py::extract_json`：先 `json.loads`，失败后扫描首个 `{...}` 或 `[...]` 的括号深度并重试。这是需要迁移掉的模型文本 JSON 截取逻辑。
- `visual_client.py::generate_json_with_images`：调用上述 `extract_json`。
- `project_manager.py` 多处 `json.loads`：解码 SQLite JSON 文本列，解析失败使用默认值。这属于持久化兼容逻辑，不应与模型 JSON 提取同时删除。
- `project_document_exporter.py`：导出时解码列表/字典字符串，失败静默保留原值。
- `case_library.py`：导入 JSON 文件，属于正常文件反序列化。
- `scripts/smoke_test.py` 的 `ast.literal_eval`：读取 `app.py` 常量，仅用于测试，不是模型输出修复。

## 7. `ProjectManager` 全部职责

`ProjectManager` 当前承担以下职责：

1. 默认数据库路径和项目输出根目录定义。
2. 创建数据库父目录和全局项目输出目录。
3. SQLite 连接工厂与 row factory。
4. 全部表、索引初始化。
5. `_ensure_columns` 式兼容迁移。
6. 项目目录与上传目录计算/创建。
7. 项目创建、列表和单项读取。
8. 项目文档登记、列表和删除。
9. 项目视觉资产保存与读取。
10. 视觉证据保存、解码与查询。
11. chunks 替换、读取和项目全文拼接。
12. chunk embeddings 保存与读取。
13. 项目画像保存与读取。
14. 需求批量替换、upsert、列表和单项读取。
15. 两种场景卡兼容写入、读取和 JSON 解码。
16. 生成上下文保存与读取。
17. 质量评分保存与读取。
18. 生成运行记录创建。
19. 生成用例保存、来源追溯写入和列表。
20. trace sources 查询。
21. 审查结果保存与查询。

它同时是 schema/migration、filesystem service、repository 集合和部分事务服务。后续应通过兼容 facade 逐步委托给 schema/migrations 和各 repository，不应一次性替换或改变数据库路径。

## 8. 检索模块重叠判断

| 模块 | 当前作用 | 是否重复 |
|---|---|---|
| `core/embedding_client.py` | 获取单条 Ollama embedding，兼容新旧两个 API | 否，是底层客户端 |
| `core/project_kb.py` | 强制传入 `project_id`，读取当前项目 chunks，执行关键词+向量混合排序并保存缺失 embedding | 否，是当前实际检索服务 |
| `rag/vector_search.py` | `vector_search_cases` 永远返回空列表，且无调用方 | 语义上与未来检索目标重叠，但当前只是未接入占位，不构成运行时重复 |

风险点：`project_kb.search_project_chunks` 每次检索都实例化 embedding 客户端并尝试网络；Ollama 不可用时一次搜索可能等待两个短超时。接口本身要求 `project_id`，当前调用均传入当前项目，但未来 Agent Tool 仍需用闭包绑定，不能暴露任意项目 ID。

## 9. protobuf 与视觉模块调用方

- `textgen_pb2.py`：仅被生成的 `textgen_pb2_grpc.py` 引用。
- `textgen_pb2_grpc.py`：没有业务调用方；使用顶层 `import textgen_pb2`，还依赖未列入 `requirements.txt` 的 `grpc`。
- `visual_client.py`：实际调用方为 `ui/knowledge_page.py` 和 `scripts/stability_check.py`；内部复用 `OllamaClient` 的 JSON 解析器。

因此 protobuf 文件是“疑似遗留但尚未确认外部引用”，视觉客户端则是当前功能必需模块。

## 10. Pydantic 模型字段问题

### 重复/同义字段

- `ScenarioItem` 与 `RequirementItem` 重复：`scenario_name`、`scenario_environment`、`initial_condition`、`trigger_event`、`expected_behavior`、`evaluation_metrics`、`test_object`、`six_quality_attribute`。
- `ScenarioItem` 与 `ScenarioCard` 表达相近但字段体系不同：`initial_condition` 对 `preconditions/system_state`，`scenario_environment` 对 `environment`，单个 `requirement_id` 对 `related_requirements`。
- `TestCaseItem` 同时有 `expected_results: List[str]` 和 `expected_result: str`。
- `TestCaseItem` 同时有 `evaluation_criteria` 和 `pass_criteria`。
- `TestCaseItem` 中 `prerequisites`、`initial_condition`、`test_condition` 含义相近。
- `TestCaseItem` 中 `test_input` 与场景化生成结果使用的 `input_data` 不一致。
- `TestCaseItem` 中 `test_type` 与场景化生成结果使用的 `case_type` 不一致。
- `TestCaseItem` 中 `scenario_name` 与场景化生成结果使用的 `related_scenario` 不一致。
- `ReviewResult.review_issues/review_suggestions` 与持久化/新链路常用的 `issues/suggestions` 不一致。
- `ScenarioCard.source_document` 是 `List[str]`，需求表是单个 `source_document: str`，生成用例字典是 `source_documents: List[str]`。

### 类型不一致

- `TestCaseItem.test_steps` 为列表，`LibraryCaseItem.test_steps` 为字符串。
- `TestCaseItem.record_items` 为列表，`LibraryCaseItem.record_items` 为字符串。
- `RequirementItem.six_quality_attribute` 为列表，`LibraryCaseItem.six_quality_attribute` 为字符串。
- `VisualEvidence.raw_response` 模型定义为字典，稳定性脚本和数据库接口可传字符串。
- 主 `AdvancedCaseGenerator` 返回的场景化用例没有对应完整 Pydantic 模型，实际使用松散字典。

### 追溯缺口

`TestCaseItem` 没有 `project_id`、`source_document(s)`、`source_chunk_ids`、`generation_run_id`、`need_human_confirm` 和 `missing_information`，而这些字段已由当前主链路实际产生。

## 11. UI 跨层访问

未发现 UI 直接导入 `sqlite3` 或调用 SQL；但 UI 广泛直接调用 `ProjectManager`，相当于直接使用数据库 gateway。

高优先级跨层点：

- `review_trace_page.py`：直接拼提示词、调用 `ollama.check_connection/generate_json`、保存审查结果和覆盖保存用例。
- `knowledge_page.py`：直接构造视觉提示词、实例化 `OllamaVisualClient`、执行模型、写视觉证据；函数内部动态导入 `project_kb`。
- `generation_page.py`：直接编排画像/需求/场景抽取、六性和测试方法匹配，并直接读写 manager。
- `project_pages.py`：旧 UI 同样直接执行抽取、检索、生成、保存和导出。
- `workbench_page.py`：直接读取多个 repository 语义的数据，并直接使用 `manager.project_dir` 检查导出目录。
- `app.py`：组合根直接创建数据库 gateway 和模型客户端是可接受方向，但异常处理和运行配置仍与 UI 状态耦合。

## 12. `except Exception`、静默 fallback 与可观测性

### 宽泛捕获并静默或弱记录

- `advanced_case_generator.py`：模型异常完全吞掉，转规则 fallback，未记录失败原因。
- `scenario_card_extractor.py`：模型/校验异常吞掉，转规则 fallback。
- `six_quality_classifier.py`：异常后 `pass`，转规则分类。
- `test_case_reviewer.py`：异常后回到规则结果，未记录原因。
- `document_ingestor.py`：embedding 生成异常吞掉，不影响入库但无诊断信息。
- `ui/knowledge_page.py`：embedding 重建异常 `pass`。
- `project_document_exporter.py`：JSON 解码失败 `pass`。

### 宽泛捕获但会展示/返回错误

- `app.py`：历史库初始化失败显示 sidebar warning。
- `ui/project_pages.py`：多个旧页面操作捕获并显示错误。
- `document_parser.py`：COM/格式解析失败后进入二进制 fallback。
- `case_library.py`：Excel 单元格转换异常带上下文处理。
- `visual_client.py`：图片处理异常转换为结构化错误。

### 确定性 fallback（应保留，但需增加原因记录）

- 画像关键字 fallback。
- 场景卡规则 fallback。
- 三套测试用例规则 fallback。
- 六性规则分类。
- 规则审查与质量评分。
- embedding 不可用时的关键词检索。

## 13. `sys.path`、循环依赖与跨层导入

### `sys.path` 修改

- `app.py`
- `scripts/smoke_test.py`
- `scripts/validate_scenario_agent.py`
- `scripts/stability_check.py`
- `scripts/create_sample_assets.py`

这是当前“从 `project/` 直接运行”布局的兼容措施。没有 `pyproject.toml` 且约束禁止新增它，迁移时需保留 CLI 可运行性。

### 延迟导入/潜在循环信号

- `document_ingestor.py` 在函数内导入 `core.project_kb.ensure_embeddings_for_chunks`；原因是 `project_kb` 又依赖 `ProjectManager`，虽然未形成直接循环，但说明入库和检索索引职责耦合。
- `document_parser.py` 在函数内导入 `requirement_parser` 的 TXT/Excel 解析函数；`requirement_parser` 本身不反向导入 `document_parser`，当前未构成循环。
- `ui/knowledge_page.py` 在按钮分支中动态导入 `ensure_embeddings_for_chunks`，属于 UI 跨层和依赖管理问题。

静态导入检查未发现明确的 Python 循环导入闭环；主要问题是 `core` 同时包含模型接入、领域服务、repository、导出和兼容代码，层次边界模糊。

## 14. 数据库访问图

```text
app.py
  ├─ ProjectManager(project_workspace.db)
  │   ├─ UI pages
  │   ├─ document_ingestor / project_kb / ContextBuilder
  │   ├─ profile/requirement/scenario extractors
  │   ├─ AdvancedCaseGenerator / ProjectCaseGenerator
  │   └─ project_document_exporter
  │
  │   project_workspace.db
  │   ├─ projects
  │   ├─ project_documents / project_chunks / project_chunk_embeddings
  │   ├─ project_assets / visual_evidence
  │   ├─ project_profiles / project_requirements / scenario_cards
  │   ├─ case_generation_contexts / generation_runs / generated_cases
  │   └─ review_results / case_quality_scores / trace_sources
  │
  └─ CaseLibraryManager(case_library.db)
      ├─ UI knowledge/generation flow
      ├─ ContextBuilder [历史写法/方法参考]
      └─ TestCaseGenerator
```

隔离现状：项目工作库查询方法普遍要求 `project_id`，主搜索只读取 `manager.list_chunks(project_id)`；smoke test 验证了 A/B 项目关键词隔离。风险在于 service/tool 层尚未封装绑定项目的上下文，调用者仍可传任意 `project_id`。

## 15. 重复代码列表

1. 三套用例生成器各自完成提示词调用、结果规范化和规则 fallback。
2. `AdvancedCaseGenerator` 与 `ProjectCaseGenerator` 都实现项目需求、chunks、六性、测试方法、来源字段和持久化运行信息的相似编排。
3. `TestCaseGenerator` 与前两者重复测试方法、历史用例、标准提示和模型失败回退逻辑。
4. 画像、场景、六性、审查分别手工约定 JSON 输出并调用同一个 `generate_json`。
5. 文本和视觉 Ollama 客户端重复 `/api/tags`、消息构造、`/api/chat` 和 JSON 输出约束。
6. 多个模块重复把字符串/列表字段规范化。
7. `ScenarioItem`、`ScenarioCard` 和嵌在 `RequirementItem` 的场景字段重复表达场景。
8. `TestCaseItem` 内部及其与新场景化用例字典存在多个同义字段。
9. `ui/navigation.py` 与旧 `ui/project_pages.py` 都实现当前项目选择逻辑。
10. 新六页面与旧 `project_pages.py` 存在项目、上传、抽取、生成、导出的页面级功能重叠。

## 16. 建议保留、迁移、废弃候选

### 保留并加测试

- `config/settings.py` 的环境配置和既有路径。
- `document_parser.py`、`document_ingestor.py` 的本地文档能力。
- 六性规则、测试方法匹配、标准知识库。
- `case_library.py` 及“历史仅参考”约束。
- `case_quality_evaluator.py` 的确定性检查。
- `project_document_exporter.py`、Excel/Word 导出能力。
- `project_kb.py` 的项目隔离和关键词 fallback。
- `visual_client.py`，直到视觉能力有兼容替代。
- 所有 schema 兼容迁移和现有数据库位置。

### 通过兼容包装器迁移

- `OllamaClient` -> `ChatOllama` 适配服务；旧方法暂保留。
- 画像/场景/审查 -> Pydantic structured-output Chain。
- `AdvancedCaseGenerator` -> `create_agent` 驱动的项目绑定测试用例 Agent facade。
- `ProjectManager` -> schema/migrations + 分领域 repository；旧类委托新实现。
- UI 内模型/提示词/保存编排 -> application service。
- 主场景化用例字典 -> 明确 Pydantic 模型和兼容字段映射。
- embedding 客户端可后续评估 `langchain-ollama` embedding，但必须保留关键词 fallback。

### 废弃候选（仅标记，不可在第一阶段删除）

- `project_case_generator.py`：调用方迁移后作为旧生成兼容层。
- `test_case_generator.py`：确认外部调用并迁移后废弃。
- `ui/project_pages.py`：确认不再有外部入口后废弃。
- `rag/vector_search.py`：确认没有计划内接口和外部导入后删除或改为正式抽象。
- `textgen_pb2.py`、`textgen_pb2_grpc.py`：确认没有外部 gRPC 部署后删除。
- 重复的 `ScenarioItem` 或旧同义字段：完成数据/导出兼容映射后再收敛。

## 17. 风险分级

### P0：必须始终阻断

- Agent Tool 接受模型提供的任意 `project_id`，导致跨项目检索或写入。
- 历史用例内容被当作当前项目事实或覆盖当前文档。
- 模型补造接口、阈值、状态或环境，而未标记“需人工确认”。
- 数据库迁移改变现有路径、删除表/列或破坏用户数据。

### P1：高风险

- 一次性替换 `ProjectManager` 或删除旧生成器，破坏 UI、脚本或外部兼容调用。
- structured output 切换时字段名/类型变化，导致导出、评分或追溯丢失。
- 移除规则 fallback 后 Ollama 不可用无法完成流程。
- UI 继续直接执行模型与提示词，项目上下文难以强制绑定。
- 未经白名单校验信任模型返回的来源 chunk/scenario/evidence ID。

### P2：中风险

- 宽泛异常吞掉真实错误，用户只看到 fallback 结果。
- embedding 每次检索尝试网络，造成离线测试和 UI 延迟。
- `ConfigDict(extra="ignore")` 静默丢弃新旧字段差异。
- 测试只覆盖离线 happy path，LangChain/Ollama 迁移容易产生未发现回归。
- gRPC 生成代码依赖未声明，若被意外导入会失败。

### P3：低风险/维护性

- 中英文命名和单复数字段不统一。
- UI 与 core 的动态导入和 `sys.path` 补丁。
- `rag/vector_search.py` 空实现造成能力误判。

## 18. 推荐迁移顺序

1. 冻结当前基线，新增针对字段兼容、项目隔离、fallback 和三套生成器入口的 characterization tests。
2. 新增 LangChain v1/`ChatOllama` 基础适配和统一 Pydantic 输出模型；保持 `OllamaClient` 兼容包装，不迁移 UI。
3. 先把画像抽取迁为 structured-output Chain，保留同一函数签名和规则 fallback。
4. 把场景抽取迁为 structured-output Chain，保留来源白名单和项目绑定。
5. 把审查迁为 Chain/application service，移除 UI 直接提示词和模型调用。
6. 为测试用例 Agent 定义只读、项目上下文闭包绑定的 tools；历史库 tool 只返回方法/写作参考。
7. 用 `langchain.agents.create_agent` 替换 `AdvancedCaseGenerator` 内部模型决策，外部接口和持久化语义保持兼容。
8. 迁移 `generation_page.py`、验证脚本及旧调用方到兼容 facade。
9. 分离 `ProjectManager` 的 schema/migrations 和 repositories，旧类继续委托。
10. 通过调用证据和回归测试确认后，最后处理旧生成器、旧页面、空 vector stub 和 protobuf 文件。

## 19. 当前测试覆盖与缺口

### `scripts/smoke_test.py`

覆盖：

- 六页面常量和未使用 `st.tabs`/旧 `project_pages`。
- 临时 SQLite 初始化和核心表存在。
- 两项目文档入库。
- A/B 项目关键词检索隔离。
- 主生成相关符号可导入。

未覆盖：模型生成、实际用例生成、画像/需求保存、场景内容、审查、导出、数据库升级、错误路径。

### `scripts/validate_scenario_agent.py`

覆盖：

- 临时 SQLite schema。
- TXT 入库。
- 规则画像、需求、场景抽取。
- `AdvancedCaseGenerator` 上下文预览和离线规则生成。
- 接口、输入、状态、判定、来源追溯。
- 质量评分保存。
- Excel/Word 导出。

未覆盖：真实 Agent（当前不存在）、Ollama/structured output、历史库、视觉证据、审查 UI、失败路径、多项目恶意输入。

### `scripts/stability_check.py`（本次未按任务要求运行）

静态审计显示其额外覆盖 TXT/PDF、视觉资产、视觉客户端返回结构、Markdown 导出和 trace sources；但不属于本次指定的三项基线命令。

### 共同缺口

- 没有 pytest 测试集和 `requirements-dev.txt`。
- 没有真实 Ollama 文本/embedding/视觉集成测试或可控 fake model 测试。
- 没有 `ProjectCaseGenerator`、`TestCaseGenerator` characterization test。
- 没有手工 JSON 异常输出、超时、半结构化输出测试。
- 没有旧数据库版本到当前 schema 的迁移测试。
- 没有并发写、事务回滚、损坏 JSON 列测试。
- 没有验证所有 SQL 都包含 `project_id` 条件的系统性测试。
- 没有历史用例事实污染测试。
- 没有未知阈值必须标记人工确认的系统性测试。
- 没有 Streamlit 页面交互测试。

## 20. 基线测试结果

执行工作目录：`project/`。

| 命令 | 结果 | 观察 |
|---|---|---|
| `python -m compileall -q .` | 通过，退出码 0 | 无输出 |
| `python scripts/smoke_test.py` | 通过，退出码 0，约 53 秒 | 六页面、项目隔离、数据库 schema 均 OK；离线环境中 embedding 双 API 超时尝试使执行较慢 |
| `python scripts/validate_scenario_agent.py` | 通过，退出码 0，约 29 秒 | 1 张场景卡，质量分 96.7；上下文、接口、输入、状态、判定、追溯、Excel、Word 均通过 |

测试脚本使用 `TemporaryDirectory` 并重定向 `PROJECT_OUTPUT_ROOT`，未写入正式项目数据库。`compileall` 按命令语义可能创建/刷新 `__pycache__` 字节码，不属于业务实现变更。

## 21. 第一阶段禁止直接删除的文件

以下文件在第一阶段不得直接删除；“无仓库内调用方”不等于没有外部兼容调用：

- `core/advanced_case_generator.py`
- `core/project_case_generator.py`
- `core/test_case_generator.py`
- `core/ollama_client.py`
- `core/visual_client.py`
- `core/embedding_client.py`
- `core/project_kb.py`
- `core/project_manager.py`
- `core/context_builder.py`
- `core/project_profile_extractor.py`
- `core/requirement_extractor.py`
- `core/scenario_card_extractor.py`
- `core/test_case_reviewer.py`
- `core/six_quality_classifier.py`
- `core/test_method_matcher.py`
- `core/case_library.py`
- `core/case_quality_evaluator.py`
- `core/project_document_exporter.py`
- `core/excel_exporter.py`
- `models/schemas.py`
- `models/visual_schemas.py`
- `ui/project_pages.py`
- `rag/vector_search.py`
- `core/textgen_pb2.py`
- `core/textgen_pb2_grpc.py`
- 现有 `prompts/*.py`
- 现有 `scripts/smoke_test.py`、`scripts/validate_scenario_agent.py`、`scripts/stability_check.py`

第一阶段应新增兼容层和 characterization tests；只有调用方迁移、外部引用确认、数据兼容验证和完整回归均完成后，才可在后续阶段提出删除。
