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
        "输入 token 预算": settings.generation_context_token_budget,
        "输出 token 预留": settings.generation_output_token_reserve,
        "上下文安全比例": settings.generation_context_safety_ratio,
        "建议最低覆盖/原子需求": settings.generation_min_cases_per_atom,
        "默认最大用例数/原子需求": settings.generation_default_max_cases_per_atom,
        "绝对最大用例数/原子需求": settings.generation_absolute_max_cases_per_atom,
        "最大用例数/模型调用": settings.generation_max_cases_per_model_call,
        "需求级异常熔断": settings.generation_hard_max_cases_per_requirement,
        "旧版最大生成秒数（兼容）": settings.generation_max_seconds,
        "单次模型调用上限秒数": settings.effective_model_call_max_seconds,
        "连续无进展超时秒数": settings.generation_idle_timeout_seconds,
        "批量任务总保险丝秒数（0=禁用）": settings.generation_batch_max_seconds,
        "HTML每页元素上限": settings.html_max_elements_per_page,
        "绑定候选页上限": settings.binding_max_candidate_pages,
        "模型服务可用": available,
        "说明": "Token 预检针对当前测试点批次；预算不足时自动缩小批次，不以最终需求总量阻断。",
    })
    safe_limit = int(settings.ollama_num_ctx * settings.generation_context_safety_ratio)
    if settings.generation_context_token_budget + settings.generation_output_token_reserve > safe_limit:
        st.warning(f"输入预算 + 输出预留超过安全上下文 {safe_limit} tokens；生成时会按证据优先级压缩，仍超限则阻断。")
    st.session_state.runtime_config = config
