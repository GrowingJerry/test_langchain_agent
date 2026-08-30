"""Persistent de-duplication for desktop notifications."""

from __future__ import annotations

import json
from pathlib import Path


class NotificationLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            self._seen = set(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else set()
        except (OSError, json.JSONDecodeError):
            self._seen = set()

    def should_notify(self, event_id: str) -> bool:
        return bool(event_id) and event_id not in self._seen

    def mark_read(self, event_id: str) -> None:
        if not event_id: return
        self._seen.add(event_id); self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(self._seen), ensure_ascii=False), encoding="utf-8")
