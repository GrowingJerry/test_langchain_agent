"""Compatibility wrapper around the shared deterministic intent router."""

from __future__ import annotations

from dataclasses import dataclass

from application.assistant.intent_router import page_url, route_intent


@dataclass(frozen=True)
class CommandResult:
    handled: bool
    reply: str
    url: str = ""


def resolve_command(text: str, status, base_url: str) -> CommandResult:
    intent = route_intent(text, base_url)
    if intent.tool == "open_streamlit_page":
        page = intent.arguments["page"]
        return CommandResult(True, f"正在打开【{page}】。", page_url(base_url, page))
    if intent.tool == "get_project_status":
        return CommandResult(True, status.message)
    if intent.tool == "cancel_current_task":
        return CommandResult(True, "当前任务只有在后端支持安全取消时才能停止；小测不会伪造成功。")
    return CommandResult(False, "")
