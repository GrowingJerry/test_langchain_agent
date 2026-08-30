"""Status projection and animation mapping for Xiaoche."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PetStatus:
    state: str
    message: str
    run_id: str = ""
    updated_at: float = 0.0


STATUS_MAP = {
    "parsing": "reading", "reading": "reading", "reasoning": "thinking",
    "running": "writing", "generating": "writing", "review_pending": "reviewing",
    "evidence_insufficient": "warning", "success": "success", "completed": "success",
    "failed": "error", "error": "error", "cancelled": "idle", "canceled": "idle",
    "idle": "idle", "sleeping": "sleeping", "dragging": "dragging",
}


def map_status(value: str) -> str:
    return STATUS_MAP.get(value.strip().lower(), "idle")


def latest_generation_status(project_root: Path) -> PetStatus:
    run_root = project_root / "logs" / "runs"
    manifests = list(run_root.glob("*/manifest.json")) if run_root.exists() else []
    if not manifests:
        return PetStatus("idle", "你好，我是小测。今天要处理什么测试任务？")
    latest = max(manifests, key=lambda path: path.stat().st_mtime)
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return PetStatus("error", f"状态读取失败：{type(exc).__name__}", updated_at=latest.stat().st_mtime)
    raw = str(payload.get("status") or "idle")
    state = map_status(raw)
    messages = {"reading": "小测正在读取文档……", "thinking": "小测正在思考……", "writing": "小测正在生成内容……", "reviewing": "有用例等待审核。", "warning": "证据不足，需要人工确认。", "success": "任务已完成。", "error": "任务失败，可查看日志。", "idle": "小测待命中。"}
    error = str(payload.get("failure_reason") or payload.get("error") or "")
    return PetStatus(state, f"任务失败：{error[:120]}" if state == "error" and error else messages.get(state, "小测待命中。"), str(payload.get("run_id") or ""), latest.stat().st_mtime)
