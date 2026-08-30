"""SQLite connection and transaction management."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from domain.exceptions import PersistenceError


class SQLiteConnectionManager:
    """Create configured SQLite connections and translate database failures."""

    def __init__(self, db_path: Path, busy_timeout_ms: int = 5000) -> None:
        self.db_path = Path(db_path)
        self.busy_timeout_ms = busy_timeout_ms
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                str(self.db_path), timeout=self.busy_timeout_ms / 1000
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
            # Streamlit and Xiaoche may read concurrently. WAL keeps readers from
            # blocking the single writer while preserving existing transactions.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            return connection
        except sqlite3.Error as exc:
            raise PersistenceError(
                f"Unable to open SQLite database {self.db_path}: {exc}"
            ) from exc

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        except sqlite3.Error as exc:
            raise PersistenceError(f"SQLite operation failed: {exc}") from exc
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN")
            yield connection
            connection.commit()
        except Exception as exc:
            connection.rollback()
            if isinstance(exc, sqlite3.Error):
                raise PersistenceError(f"SQLite transaction failed: {exc}") from exc
            raise
        finally:
            connection.close()
