"""Shared repository helpers."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from infrastructure.database.connection import SQLiteConnectionManager


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class BaseRepository:
    def __init__(self, connections: SQLiteConnectionManager) -> None:
        self.connections = connections
