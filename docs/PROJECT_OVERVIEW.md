# 项目级测试文档智能生成系统总览

## 1. 项目定位

本项目是一个本地运行的测试文档辅助生成工具。系统以“项目”为隔离单位，支持上传项目资料、构建项目知识库、抽取需求和场景，并基于当前项目证据生成可审查、可追溯、可导出的测试用例和测试文档初稿。

系统当前仍属于工程演进阶段。生成内容应作为测试设计和文档编制辅助，不能替代人工审核。

## 2. 核心功能

- 多项目管理和项目级数据隔离。
- TXT、Markdown、DOCX、PDF 文档上传、解析和切分。
- 当前项目知识库检索，支持关键词检索与 Ollama embedding 混合检索。
- 项目画像、需求点、场景卡抽取。
- 历史用例库参考，主要用于格式和方法参考。
- 场景化测试用例生成。
- 测试用例质量评分。
- 文档片段、需求、用例、视觉证据追溯。
- Excel、Word、Markdown 导出。
- 图片资料上传和视觉证据抽取，当前为实验性能力。

## 3. 六个页面说明

| 页面 | 说明 |
|---|---|
| 项目工作台 | 新建或选择项目，查看资料、需求、用例、追溯和导出状态。 |
| 文档与知识库 | 上传项目文档，查看文档列表、知识库 chunks、检索结果、历史用例库，以及图片视觉证据抽取测试区。 |
| 智能生成 | 抽取项目画像、需求点、场景卡，预览生成上下文，并生成场景化测试用例。 |
| 结果审查与追溯 | 查看质量评分、执行规则审查、人工修改用例，查看文档片段和视觉证据来源。 |
| 导出中心 | 导出项目级 Excel、Word 初稿、Markdown，以及专项表格。 |
| 系统设置 | 配置 Ollama、模型、Top-K、历史用例库、SQLite 路径、输出目录和调试模式。 |

不再新增一级页面。新增能力应优先嵌入上述六个页面。

## 4. 系统处理流程

1. 创建或选择项目。
2. 上传项目文档或图片资料。
3. 文档解析并切分为项目 chunks。
4. 为 chunks 构建关键词索引和可选 embedding。
5. 抽取项目画像。
6. 抽取需求点。
7. 生成场景卡。
8. 构建生成上下文：文档证据、需求点、场景卡、历史用例参考、视觉证据。
9. 生成测试用例。
10. 执行质量评分和规则审查。
11. 查看追溯来源并人工确认。
12. 导出 Excel、Word 或 Markdown。

资料不足时，系统会在结果中标记 `need_human_confirm` 或 `missing_information`。

## 5. 文档知识库机制

文档上传后会保存到当前项目目录，并写入 `project_documents`。可解析文本会切分为 `project_chunks`。

当前支持：

- TXT
- Markdown
- DOCX
- 可复制文本 PDF

PDF 解析优先使用 PyMuPDF，即 `fitz`。扫描 PDF、空 PDF 或解析失败 PDF 会给出 warning。OCR 和视觉模型解析扫描件仍属于待完善能力。

chunk 记录尽量保留：

- `document_id`
- `project_id`
- `page_no`
- `chunk_index`
- `chunk_text`
- `source_type`

知识库检索默认只检索当前 `project_id`，避免跨项目混用。

## 6. 历史用例参考机制

历史用例库是全局参考库，保存在 SQLite 中。它用于提供测试方法、步骤写法和格式参考，不作为当前项目事实来源。

约束：

- 历史用例不能覆盖当前项目文档。
- 当前项目需求和文档证据优先。
- 默认只有用户启用历史库时才参与生成上下文。

## 7. Ollama 文本模型配置

文本生成默认使用 Ollama 本地模型。

常用环境变量：

```env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:8b
OLLAMA_TIMEOUT=120
ENABLE_OLLAMA=true
```

拉取示例：

```powershell
ollama pull qwen3:8b
```

如果 Ollama 不可用，系统会尽量退化到规则生成或规则抽取，但生成质量会降低。

## 8. Ollama Embedding 模型配置

知识库检索支持“关键词检索 + embedding 向量检索”的混合检索。

常用环境变量：

```env
OLLAMA_EMBED_MODEL=nomic-embed-text
```

拉取示例：

```powershell
ollama pull nomic-embed-text
```

embedding 数据保存在 `project_chunk_embeddings`。如果 embedding 服务不可用，系统会自动退化为关键词检索，不影响基础使用。

当前混合评分策略较简单：

```text
final_score = 0.4 * keyword_score + 0.6 * vector_score
```

该策略后续可继续优化。

## 9. Ollama 视觉模型 qwen2.5vl 配置

视觉模型用于图片证据抽取，当前为实验性能力。

常用环境变量：

```env
OLLAMA_VISION_MODEL=qwen2.5vl:3b
OLLAMA_VISION_TIMEOUT=600
OLLAMA_VISION_IMAGE_MAX_SIDE=896
```

拉取示例：

```powershell
ollama pull qwen2.5vl:3b
```

注意事项：

- 视觉模型在 CPU 上可能很慢，单张截图可能需要 1 到 5 分钟。
- `qwen2.5vl:3b` 在部分 Ollama / ggml 组合下，对高分辨率或非标准比例截图可能触发底层断言。
- 系统会在发送前将图片转为 RGB JPEG，并缩放到方形画布，以降低失败概率。
- 如果仍频繁失败，可尝试 `qwen3-vl:8b`，但资源占用更高。

## 10. 视觉证据抽取机制

视觉模型不直接生成测试用例，而是先抽取结构化视觉证据 `VisualEvidence`。

视觉证据包含：

- 图片类型
- 主要对象
- 可见文字
- 可能功能
- 可能测试点
- 风险点
- 置信度
- 是否需要人工确认
- 原始模型响应

约束：

- 不编造图片中不存在的文字。
- 不确定内容写入 `risk_points` 或标记 `need_human_confirm=true`。
- 视觉证据只能作为测试设计辅助，不能覆盖文本需求。
- 标记为需要人工确认的视觉证据，不能作为关键用例的唯一依据。

相关表：

- `project_assets`
- `project_visual_evidence`

所有视觉资产和证据都带 `project_id`。

## 11. 测试用例生成逻辑

测试用例生成主要使用当前项目上下文：

- 项目画像
- 当前需求点
- 相关文档 chunks
- 相关场景卡
- 测试方法匹配结果
- 历史用例参考
- 视觉证据，实验性辅助

生成规则：

- 当前项目文档和明确需求优先。
- 历史用例只参考写法和方法。
- 视觉证据只补充 UI 控件、流程节点、界面状态等辅助测试点。
- 如果文本需求和视觉证据冲突，以文本需求为准，并标记不确定信息。
- 资料缺失时标记 `missing_information` 或 `need_human_confirm`。

生成结果会尽量保留：

- `requirement_id`
- `source_documents`
- `source_chunk_ids`
- `evidence_sources`
- `assumptions`
- `missing_information`

## 12. 质量评分逻辑

质量评分是规则型评分，当前不是正式质量认证。

主要维度包括：

- 需求覆盖度
- 场景贴合度
- 步骤可执行性
- 判定准则明确性
- 来源可追溯性
- 是否存在无来源数值指标
- 视觉证据使用合理性，实验性

视觉证据评分规则：

- 没有视觉证据时不扣分。
- 使用视觉证据但缺少 `evidence_id` 会扣分。
- 使用 `need_human_confirm=true` 的证据作为唯一依据会扣分。
- 视觉证据与文本需求冲突但未标记冲突会扣分。
- 视觉证据合理补充 UI 控件、流程节点、界面状态时可加分。

评分结果用于提示人工审查重点，不应作为唯一验收标准。

## 13. 追溯机制

系统支持多层追溯：

- 需求追溯到文档和 chunk。
- 用例追溯到需求、文档 chunk 和生成上下文。
- 用例可追溯到视觉证据 `evidence_id`。
- 导出文件包含需求、用例和来源信息。

主要表：

- `trace_sources`
- `generated_cases`
- `case_generation_contexts`
- `project_visual_evidence`

追溯数据均带 `project_id`，默认不跨项目展示。

## 14. 本地部署步骤

建议使用独立虚拟环境。

```powershell
cd F:\pythonproject\test-agent-helper-main
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r project\requirements.txt
```

如果使用 conda：

```powershell
conda create -n sixqa python=3.10
conda activate sixqa
python -m pip install -r project\requirements.txt
```

启动 Ollama：

```powershell
ollama serve
```

拉取模型：

```powershell
ollama pull qwen3:8b
ollama pull nomic-embed-text
ollama pull qwen2.5vl:3b
```

启动 Streamlit：

```powershell
streamlit run project\app.py
```

可复制 `.env.example` 为 `project/.env` 后调整本地配置。

## 15. 常见问题

### PDF 上传后没有正文 chunks

确认安装的是 PyMuPDF，而不是错误的 `fitz` 包：

```powershell
python -m pip uninstall fitz -y
python -m pip install PyMuPDF
```

扫描 PDF 暂不做 OCR，后续可接入 OCR 或视觉模型。

### Ollama 视觉模型返回 500

常见原因包括模型正在 CPU 慢速推理、图片尺寸触发底层兼容问题、Ollama 服务端崩溃或资源不足。

建议：

```env
OLLAMA_VISION_TIMEOUT=600
OLLAMA_VISION_IMAGE_MAX_SIDE=896
```

必要时重启 Ollama，并尝试更稳定的视觉模型。

### 视觉证据抽取很慢

如果 `ollama ps` 显示 `100% CPU`，说明模型在 CPU 上推理。可考虑使用 GPU 环境，或降低 `OLLAMA_VISION_IMAGE_MAX_SIDE`。

### 生成结果标记“需人工确认”

说明项目资料中缺少输入数据、判定阈值、接口、环境、场景状态等关键信息。补充资料后重新抽取和生成。

### 历史用例会污染当前项目吗

不会。历史用例只作为格式和方法参考。当前项目事实只来自当前 `project_id` 的文档、需求、场景和视觉证据。

### NumPy、numexpr 或 bottleneck 出现兼容提示

建议使用干净虚拟环境并按 `requirements.txt` 安装。如果仍出现 `_ARRAY_API` 错误，请重新安装与当前 NumPy 版本匹配的依赖，或使用 `numpy<2`。

## 16. 后续路线图

短期：

- 增强 PDF 解析状态提示和页面反馈。
- 为视觉证据增加人工确认、驳回和备注状态。
- 增加图片压缩参数的页面配置。
- 补充更多稳定性检查脚本。

中期：

- 接入 OCR 或视觉模型处理扫描 PDF。
- 优化混合检索评分和 chunk rerank。
- 增强生成 prompt 对冲突证据的处理。
- 将质量评分规则配置化。

长期：

- 支持测试大纲、测试说明、测试记录、测试报告的更完整生成链路。
- 支持更细粒度的需求覆盖矩阵和变更影响分析。
- 支持项目级证据审核工作流。
- 引入更完善的自动化测试和迁移管理。

