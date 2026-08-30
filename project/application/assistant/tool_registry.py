"""Validated whitelist of deterministic assistant tools with JSONL audit logs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from .tool_result import ToolResult


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolRegistry:
    def __init__(self, audit_path: Path) -> None:
        self.audit_path = audit_path
        self._tools: dict[str, tuple[type[BaseModel], Callable[..., Any]]] = {}

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def register(self, name: str, schema: type[BaseModel], handler: Callable[..., Any]) -> None:
        if not name or name.startswith("_") or name in self._tools:
            raise ValueError(f"无效或重复的工具名：{name}")
        self._tools[name] = (schema, handler)

    def execute(self, session_id: str, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        started = datetime.now(timezone.utc)
        if name not in self._tools:
            result = ToolResult(False, "拒绝执行未注册工具", error="tool_not_allowed")
            self._audit(session_id, name, arguments or {}, started, result)
            return result
        schema, handler = self._tools[name]
        try:
            validated = schema.model_validate(arguments or {})
            value = handler(**validated.model_dump())
            result = value if isinstance(value, ToolResult) else ToolResult(True, "执行成功", {"result": value})
        except ValidationError as exc:
            result = ToolResult(False, "工具参数校验失败", error=str(exc))
        except Exception as exc:  # error is returned and audited, never hidden
            result = ToolResult(False, "工具执行失败", error=f"{type(exc).__name__}: {exc}")
        self._audit(session_id, name, arguments or {}, started, result)
        return result

    def _audit(self, session_id: str, name: str, arguments: dict, started: datetime, result: ToolResult) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "session_id": session_id,
            "tool": name,
            "arguments": {key: "<redacted>" if any(x in key.lower() for x in ("token", "password", "secret")) else str(value)[:300] for key, value in arguments.items()},
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": "success" if result.success else "error",
            "result": result.message[:500],
            "error": result.error[:1000],
        }
        with self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
