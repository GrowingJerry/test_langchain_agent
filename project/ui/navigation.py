"""Final application navigation helpers."""

from typing import Any, Optional

import streamlit as st

LEGACY_UNSCOPED_STATE = (
    "requirements",
    "scenarios",
    "six_map",
    "test_cases",
    "reviews",
    "scenario_input",
    "project_generated_case",
    "export_excel",
    "export_word",
)


def _activate_project(project_id: str) -> None:
    """Activate a project and discard only obsolete unscoped workflow state."""
    previous = st.session_state.get("active_project_scope", "")
    if previous != project_id:
        for key in LEGACY_UNSCOPED_STATE:
            st.session_state.pop(key, None)
        st.session_state.active_project_scope = project_id
    st.session_state.current_project_id = project_id


def ensure_current_project(service: Any) -> Optional[str]:
    """Keep the selected project valid without leaking page state across projects."""
    project_ids = [row["project_id"] for row in service.list_projects()]
    current = st.session_state.get("current_project_id", "")
    if current not in project_ids:
        st.session_state.current_project_id = project_ids[0] if project_ids else ""
    return st.session_state.current_project_id or None


def render_project_selector(service: Any) -> Optional[str]:
    """Render the single global project selector."""
    projects = service.list_projects()
    if not projects:
        st.info("尚未创建项目，请进入“项目工作台”新建项目。")
        return None
    by_id = {row["project_id"]: row for row in projects}
    current = ensure_current_project(service)
    pending = st.session_state.pop("pending_project_id", "")
    if pending in by_id:
        current = pending
        _activate_project(pending)
        st.session_state.project_selector = pending
    elif st.session_state.get("project_selector") not in by_id:
        st.session_state.project_selector = current
    selected = st.selectbox(
        "当前项目",
        list(by_id),
        format_func=lambda project_id: (
            f"{by_id[project_id]['project_name']} ({project_id})"
        ),
        key="project_selector",
    )
    _activate_project(selected)
    return selected
