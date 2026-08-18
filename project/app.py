# -*- coding: utf-8 -*-
"""项目级测试文档智能生成系统的 Streamlit 统一入口。"""

from pathlib import Path

import streamlit as st
from infrastructure.runtime_diagnostics import logger

from config import settings
from application.services.project_service import DEFAULT_PROJECT_DB
from application.container import ApplicationContainer
from ui.navigation import ensure_current_project, render_project_selector
from ui.workbench_page import render_workbench_page
from ui.knowledge_page import render_knowledge_page
from ui.generation_page import render_generation_page
from ui.review_trace_page import render_review_trace_page
from ui.export_page import render_export_page
from ui.settings_page import render_settings_page


PAGES = [
    "项目工作台",
    "资料与需求",
    "生成测试用例",
    "用例审查",
    "导出中心",
    "系统设置",
]


def _runtime_config() -> dict:
    defaults = {
        "ollama_url": settings.OLLAMA_BASE_URL,
        "model": settings.OLLAMA_MODEL,
        "use_ollama": settings.ENABLE_OLLAMA,
        "use_agent": settings.settings.enable_agent,
        "use_library": settings.ENABLE_CASE_LIBRARY,
        "use_ollama_review": False,
        "top_k": settings.TOP_K_CASES,
        "project_db": str(DEFAULT_PROJECT_DB),
        "library_db": str(settings.DEFAULT_CASE_LIBRARY_DB),
        "output_dir": str(settings.OUTPUTS_DIR),
        "debug": False,
    }
    if "runtime_config" not in st.session_state:
        st.session_state.runtime_config = defaults
    else:
        for key, value in defaults.items():
            st.session_state.runtime_config.setdefault(key, value)
    return st.session_state.runtime_config


def _settings_from_runtime_config(config: dict) -> settings.Settings:
    return settings.settings.model_copy(
        update={
            "ollama_base_url": config["ollama_url"],
            "ollama_model": config["model"],
            "enable_ollama": bool(config["use_ollama"]),
            "enable_agent": bool(config.get("use_agent", False)),
            "enable_case_library": bool(config["use_library"]),
            "top_k_cases": int(config["top_k"]),
        }
    )


def main() -> None:
    st.set_page_config(
        page_title="项目级测试文档智能生成系统", page_icon="🧭", layout="wide"
    )
    config = _runtime_config()
    runtime_settings = _settings_from_runtime_config(config)
    library_db = Path(config["library_db"]) if config["use_library"] else None
    service = ApplicationContainer().build_ui_service(
        Path(config["project_db"]), library_db, runtime_settings
    )
    case_library = service.case_library
    ensure_current_project(service)

    with st.sidebar:
        st.title("测试文档智能生成")
        st.caption("以当前项目资料为事实源")
        project_id = render_project_selector(service)
        st.divider()
        page = st.radio("主导航", PAGES, label_visibility="collapsed")
        st.divider()
        st.caption(f"Ollama：{'启用' if config['use_ollama'] else '关闭'}")
        st.caption(f"Agent：{'启用' if config.get('use_agent') else '关闭'}")
        st.caption(
            f"历史用例库：{'启用（仅参考）' if config['use_library'] else '关闭'}"
        )

    st.title("项目级测试文档智能生成系统")
    if page == "项目工作台":
        render_workbench_page(service, project_id)
    elif page == "资料与需求":
        render_knowledge_page(
            service,
            project_id,
            case_library,
            int(config["top_k"]),
            bool(config["use_ollama"]),
        )
    elif page == "生成测试用例":
        render_generation_page(service, project_id, case_library, config)
    elif page == "用例审查":
        render_review_trace_page(service, project_id, config)
    elif page == "导出中心":
        render_export_page(service, project_id)
    else:
        render_settings_page(config)
    if config["debug"]:
        with st.sidebar.expander("调试信息"):
            st.json({"project_id": project_id, "page": page, "config": config})


if __name__ == "__main__":
    try:
        main()
    except MemoryError:
        logger.exception("Streamlit request failed due to memory exhaustion")
        st.error("内存不足，已中止本次操作。后台仍保持运行，请缩小单次显示范围并查看 logs/streamlit.log。")
    except SystemExit as exc:
        logger.exception("Unexpected SystemExit in Streamlit request: %s", exc)
        st.error(f"后台任务异常退出：SystemExit({exc})。详情见 logs/streamlit.log。")
    except Exception as exc:
        logger.exception("Unhandled Streamlit request error")
        st.error(f"请求失败但服务仍在运行：{type(exc).__name__}: {exc}")
