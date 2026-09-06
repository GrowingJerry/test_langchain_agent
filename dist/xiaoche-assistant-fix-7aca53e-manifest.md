# 小测附件工具链增量包 7aca53e

基准提交：`cf8a954`  
修复提交：`7aca53e`

## 需要替换的文件

- `project/application/assistant/gateway.py`
- `project/application/assistant/session_store.py`
- `project/desktop_pet/chat_panel.py`
- `project/desktop_pet/ollama_chat.py`
- `project/desktop_pet/window.py`
- `project/docs/xiaoche-desktop.md`
- `project/infrastructure/database/migrations.py`
- `project/infrastructure/documents/assistant_ingestor.py`

## 需要新增的文件

- `project/application/assistant/traceability.py`
- `project/tests/unit/test_xiaoche_attachment_pipeline.py`
- `project/tests/unit/test_xiaoche_qt_attachments.py`

## 不要覆盖的文件和目录

- `project/.env`
- `project/data/`
- `project/outputs/`（包括其中的 SQLite 业务数据库）
- `project/logs/`
- `project/vendor/`
- `project/wheelhouse/`
- 用户上传文件、Ollama 模型和浏览器缓存

## 新增依赖

本次修复代码未在 `cf8a954` 基础上新增第三方包，但完整桌面/PDF环境的 wheelhouse 必须包含：

- `PySide6`
- `PySide6_Addons`
- `PySide6_Essentials`
- `shiboken6`
- `PyMuPDF`
- `python-docx`
- `openpyxl`

## 数据库迁移

`ProjectManager` 启动时自动调用现有幂等迁移。迁移新增 `assistant_attachments`、`assistant_tool_runs`、`assistant_outputs`，并为 `assistant_messages` 增加 `attachment_ids_json`；不会删除旧数据，也不要求重建数据库。

升级前停止程序并复制 `project/outputs/` 下的 SQLite 文件。启动后可用 SQLite 工具确认上述表和列存在，或运行 `scripts/check-xiaoche.ps1` 后执行专项测试。若迁移失败，立即停止程序，恢复数据库备份和旧代码；不要删除数据库重建。

## 内网安装命令

```powershell
cd project
python -m pip install --no-index --find-links wheelhouse -r requirements-desktop.txt
```

## 内网替换步骤

1. 停止当前小测和 Streamlit。
2. 备份数据库、`.env` 和旧代码。
3. 解压本增量包到临时目录。
4. 仅覆盖“需要替换的文件”，并复制“需要新增的文件”。
5. 从内网 wheelhouse 安装上述依赖。
6. 执行 `powershell -ExecutionPolicy Bypass -File project/scripts/check-xiaoche.ps1`。
7. 在 `project` 下执行 `python -m streamlit run app.py`。
8. 执行 `python -m desktop_pet.app`。
9. 上传含需求编号和表格的 DOCX，输入“帮我解析这个文件，转化成Word版本的需求追踪矩阵”，检查输出及工具日志。
10. 失败时停止程序，恢复旧代码和 SQLite 备份。

## 包内容限制

包内不含 `.git`、`.env`、SQLite 数据库、`outputs`、`logs`、上传文件、模型、缓存、`__pycache__`、`.pytest_cache`、临时文件或测试生成文档。
