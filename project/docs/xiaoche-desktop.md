# 小测桌面智能体 2.0

小测通过 `ApplicationContainer` 使用与 Streamlit 相同的应用服务、项目 SQLite、Ollama 配置、测试用例生成与正式导出器。桌宠不直接拼 SQL，也不让模型执行本机代码。附件先复制到 `work/assistant/<session>/attachments`，由本地解析器限额读取；助手文档输出到 `outputs/assistant/<session>/` 并强制回读。

## 在线机器准备离线 wheelhouse

```powershell
python -m pip download -r requirements.txt -r requirements-desktop.txt -d wheelhouse
```

把仓库、`.env`、`wheelhouse` 和已准备好的 Ollama 模型复制到内网机器，然后执行：

```powershell
python -m pip install --no-index --find-links wheelhouse -r requirements-desktop.txt
```

## 启动与检查

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check-xiaoche.ps1
powershell -ExecutionPolicy Bypass -File scripts/start-xiaoche.ps1
powershell -ExecutionPolicy Bypass -File scripts/start-xiaoche-all.ps1
```

一键脚本只终止自己启动的 Streamlit；若端口已由用户服务占用，不会重复启动或关闭它。

## 打包

```powershell
python -m PyInstaller --clean --noconfirm xiaoche.spec
```

产物位于 `dist/小测.exe`，不提交仓库。内网迁移需复制 `project/`、`.env`、`wheelhouse/`、Ollama 模型目录；数据库和已上传资料按需复制 `outputs/sqlite/`、`data/`，助手历史输出按需复制 `outputs/assistant/`。
