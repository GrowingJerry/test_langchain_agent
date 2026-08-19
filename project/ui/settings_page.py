"""Runtime system settings page."""

import streamlit as st
from config.settings import settings
from infrastructure.llm.ollama_health import OllamaHealthClient
from domain.rules.standard_knowledge import STANDARD_DOCX


def render_settings_page(config: dict) -> None:
    st.header("系统设置")
    st.caption("设置保存在当前浏览器会话中；环境变量仍是重启后的默认值。")
    config["ollama_url"] = st.text_input("Ollama 地址", config["ollama_url"])
    config["model"] = st.text_input("模型选择", config["model"])
    config["use_ollama"] = st.checkbox("启用 Ollama", config["use_ollama"])
    config["use_agent"] = st.checkbox(
        "启用 Agent（需要 Ollama 和 langchain-ollama）",
        bool(config.get("use_agent", False)),
        disabled=not config["use_ollama"],
    )
    config["use_library"] = st.checkbox("启用历史用例库", config["use_library"])
    config["top_k"] = st.number_input("Top-K 相似用例", 1, 20, config["top_k"])
    st.text_input("项目 SQLite 路径", config["project_db"], disabled=True)
    st.text_input("历史用例 SQLite 路径", config["library_db"], disabled=True)
    st.text_input("输出路径", config["output_dir"], disabled=True)
    st.text_input("标准库设置", str(STANDARD_DOCX), disabled=True)
    config["use_ollama_review"] = st.checkbox(
        "审查时启用 Ollama", config["use_ollama_review"]
    )
    config["debug"] = st.checkbox("调试模式", config["debug"])
    health = OllamaHealthClient(settings)
    available = health.is_available()
    st.subheader("本次模型请求配置")
    st.json({
        "当前测试用例模型": settings.test_case_model,
        "配置的 num_ctx": settings.ollama_num_ctx,
        "实际请求 num_ctx": settings.ollama_num_ctx,
        "num_predict": settings.ollama_structured_num_predict,
        "超时秒数": settings.ollama_timeout,
        "模型服务可用": available,
        "说明": "生成前会显示输入 token 估算；可能超限时按证据优先级压缩，仍超限则阻断。",
    })
    st.session_state.runtime_config = config
