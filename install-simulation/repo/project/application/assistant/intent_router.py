"""Conservative deterministic routing before an LLM is considered."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote


PAGES = ("项目工作台", "资料与需求", "生成测试用例", "用例审查", "导出中心", "系统设置")
ALIASES = {"工作台": PAGES[0], "资料": PAGES[1], "需求": PAGES[1], "生成": PAGES[2], "审查": PAGES[3], "审核": PAGES[3], "导出": PAGES[4], "设置": PAGES[5]}


@dataclass(frozen=True)
class Intent:
    tool: str = ""
    arguments: dict | None = None


def route_intent(text: str, base_url: str = "http://127.0.0.1:8501") -> Intent:
    value = "".join(text.strip().lower().split())
    if any(token in value for token in ("停止当前任务", "取消当前任务")):
        return Intent("cancel_current_task", {})
    if "打开" in value or "进入" in value:
        for alias, page in ALIASES.items():
            if alias in value:
                return Intent("open_streamlit_page", {"page": page, "base_url": base_url})
    if "项目" in value and any(token in value for token in ("状态", "进度", "哪一步")):
        return Intent("get_project_status", {})
    if "等待人工" in value or "待确认" in value:
        return Intent("get_case_review_summary", {})
    if "导出" in value and "excel" in value:
        return Intent("export_project_excel", {})
    return Intent()


def page_url(base_url: str, page: str) -> str:
    if page not in PAGES:
        raise ValueError("未知页面")
    return f"{base_url.rstrip('/')}?page={quote(page)}"
