"""Bounded SQLite conversation history in the existing project database."""

from __future__ import annotations

import json
from datetime import datetime, timezone


class SessionStore:
    def __init__(self, manager) -> None:
        self.manager = manager
        with manager.connections.connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS assistant_sessions(session_id TEXT PRIMARY KEY,title TEXT NOT NULL,summary TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS assistant_messages(message_id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,metadata_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,FOREIGN KEY(session_id) REFERENCES assistant_sessions(session_id))")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_assistant_messages_session ON assistant_messages(session_id,message_id)")

    def add(self, session_id: str, role: str, content: str, metadata: dict | None = None) -> None:
        if role not in {"user", "assistant", "tool"} or not content.strip():
            raise ValueError("消息角色或内容无效")
        now = datetime.now(timezone.utc).isoformat()
        with self.manager.connections.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO assistant_sessions(session_id,title,created_at,updated_at) VALUES(?,?,?,?)", (session_id, "小测会话", now, now))
            conn.execute("INSERT INTO assistant_messages(session_id,role,content,metadata_json,created_at) VALUES(?,?,?,?,?)", (session_id, role, content, json.dumps(metadata or {}, ensure_ascii=False), now))
            conn.execute("UPDATE assistant_sessions SET updated_at=? WHERE session_id=?", (now, session_id))

    def page(self, session_id: str, limit: int = 50, before_id: int | None = None) -> list[dict]:
        limit = max(1, min(limit, 100))
        query = "SELECT * FROM assistant_messages WHERE session_id=?"
        args: list = [session_id]
        if before_id is not None:
            query += " AND message_id<?"
            args.append(before_id)
        query += " ORDER BY message_id DESC LIMIT ?"
        args.append(limit)
        with self.manager.connections.connection() as conn:
            return [dict(row) for row in conn.execute(query, args).fetchall()][::-1]

    def context(self, session_id: str, budget: int = 12000, max_messages: int = 24) -> list[dict]:
        rows = self.page(session_id, max_messages)
        selected, used = [], 0
        for row in reversed(rows):
            content = row["content"]
            if used + len(content) > budget:
                content = content[-max(0, budget - used):]
            if content:
                selected.append({"role": row["role"], "content": content})
                used += len(content)
            if used >= budget:
                break
        return selected[::-1]
