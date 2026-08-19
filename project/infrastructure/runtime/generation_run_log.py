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
    def __init__(self, project_root: Path, project_id: str) -> None:
        self.run_id = "RUN-" + uuid.uuid4().hex[:12].upper()
        self.project_id = project_id
        self.root = project_root / "logs" / "runs" / self.run_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_path = self.root / "run.log"
        self._write("manifest.json", {"run_id": self.run_id, "project_id": project_id, "started_at": self.now(), "status": "running"})

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _safe(value: Any) -> Any:
        text = json.dumps(value, ensure_ascii=False, default=str)
        text = re.sub(r"(?i)(password|token|secret|api[_-]?key)\s*[=:]\s*[^,\s\"}]+", r"\1=[redacted]", text)
        if len(text.encode("utf-8")) > 2_000_000:
            text = text[:1_000_000] + '"…[diagnostic artifact bounded]"'
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"bounded_text": text}

    def _write(self, name: str, value: Any) -> None:
        (self.root / name).write_text(json.dumps(self._safe(value), ensure_ascii=False, indent=2), encoding="utf-8")

    def artifact(self, name: str, value: Any) -> None:
        self._write(name, value)

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
        self._write("manifest.json", {"run_id": self.run_id, "project_id": self.project_id, "ended_at": self.now(), "status": status, **details})

    def diagnostic_zip(self) -> Path:
        target = self.root / f"{self.run_id}-diagnostics.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in self.root.iterdir():
                if path.is_file() and path != target and path.stat().st_size <= 2_000_000:
                    archive.write(path, path.name)
        return target
