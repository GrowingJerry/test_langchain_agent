"""Bounded, UTF-8 diagnostic artifacts for one generation run."""

from __future__ import annotations

import json
import logging
import re
import traceback
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class GenerationRunLog:
    def __init__(self, project_root: Path, project_id: str, *, settings: Any = None,
                 operation_type: str = "test_case_generation", operation_name: str = "测试用例生成",
                 project_name: str = "", requirement_id: str = "", requirement_name: str = "",
                 test_type: str = "", generation_mode: str = "", model: str = "") -> None:
        self.run_id = "RUN-" + uuid.uuid4().hex[:12].upper()
        self.project_id = project_id
        self.settings = settings
        self.started_at = self.now()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_req = re.sub(r"[^A-Za-z0-9_\-\u4e00-\u9fff]+", "_", requirement_id or "NO_REQ")[:80]
        self.readable_label = f"{stamp}__{operation_name}__{safe_req}__{self.run_id}"
        self.root = project_root / "logs" / "runs" / self.readable_label
        self.root.mkdir(parents=True, exist_ok=True)
        self._apply_retention(self.root.parent)
        self.log_path = self.root / "run.log"
        self.manifest = {"run_id": self.run_id, "operation_type": operation_type,
            "operation_name": operation_name, "project_id": project_id, "project_name": project_name,
            "requirement_id": requirement_id, "requirement_name": requirement_name, "test_type": test_type,
            "generation_mode": generation_mode, "model": model, "started_at": self.started_at,
            "ended_at": "", "status": "running", "case_ids": [], "failure_reason": ""}
        self._write("manifest.json", self.manifest)
        self.event("BATCH_STARTED", "generation run log created")

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _safe(self, value: Any) -> Any:
        text = json.dumps(value, ensure_ascii=False, default=str)
        if not self.settings or self.settings.run_log_redact_sensitive_data:
            text = re.sub(r"(?i)(password|token|secret|api[_-]?key)\s*[=:]\s*[^,\s\"}]+", r"\1=[redacted]", text)
        max_bytes = getattr(self.settings, "run_log_max_file_bytes", 2_000_000)
        if len(text.encode("utf-8")) > max_bytes:
            text = text[:max_bytes // 2] + '"…[diagnostic artifact bounded]"'
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"bounded_text": text}

    def _write(self, name: str, value: Any) -> None:
        (self.root / name).write_text(json.dumps(self._safe(value), ensure_ascii=False, indent=2), encoding="utf-8")

    def artifact(self, name: str, value: Any) -> None:
        if name.startswith("03-model-request") and self.settings and not self.settings.run_log_save_model_request: return
        if name.startswith("04-model") and self.settings and not self.settings.run_log_save_model_response: return
        self._write(name, value)

    def _apply_retention(self, run_root: Path) -> None:
        if not self.settings: return
        now=datetime.now().timestamp(); cutoff=now-self.settings.run_log_retention_days*86400
        directories=sorted((p for p in run_root.iterdir() if p.is_dir()),key=lambda p:p.stat().st_mtime,reverse=True)
        for path in directories[self.settings.run_log_max_files:]:
            if path.stat().st_mtime < cutoff:
                for child in path.iterdir():
                    if child.is_file(): child.unlink(missing_ok=True)
                try: path.rmdir()
                except OSError: pass

    def event(self, stage: str, message: str, **details: Any) -> None:
        line = f"[{self.run_id}][{stage}] {message}"
        if details:
            line += " " + json.dumps(self._safe(details), ensure_ascii=False)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{self.now()} {line}\n")
        logging.getLogger("test_agent.generation").info(line)

    def failure(self, stage: str, exc: BaseException, **details: Any) -> None:
        self.event(stage, f"失败：{type(exc).__name__}: {exc}", **details)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(traceback.format_exc() + "\n")

    def finish(self, status: str, **details: Any) -> None:
        if self.manifest.get("status") != "running":
            return
        self.manifest.update({"ended_at": self.now(), "status": status, **details})
        if "error" in details and not self.manifest.get("failure_reason"):
            self.manifest["failure_reason"] = str(details["error"])
        self._write("manifest.json", self.manifest)
        self.event("BATCH_" + status.upper(), "generation run finished", **details)

    def __del__(self) -> None:
        """Best-effort terminal marker for exception paths that escape a caller."""
        try:
            if getattr(self, "manifest", {}).get("status") == "running":
                self.finish("failed", failure_reason="unhandled_exception_or_abandoned_run")
        except Exception:
            pass

    def diagnostic_zip(self) -> Path:
        target = self.root / f"{self.run_id}-diagnostics.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in self.root.iterdir():
                if path.is_file() and path != target and path.stat().st_size <= getattr(self.settings, "run_log_max_file_bytes", 2_000_000):
                    archive.write(path, path.name)
        return target
