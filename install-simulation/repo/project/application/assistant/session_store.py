"""Persistent assistant sessions, attachment links, tool runs and outputs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, manager) -> None:
        self.manager = manager

    def ensure_session(self, session_id: str, title: str = "小测会话") -> None:
        now = now_iso()
        with self.manager.connections.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO assistant_sessions(session_id,title,created_at,updated_at) VALUES(?,?,?,?)",
                (session_id, title, now, now),
            )

    def add_message(self, session_id: str, role: str, content: str, attachment_ids: list[str] | None = None, metadata: dict | None = None) -> int:
        if role not in {"user", "assistant", "tool"} or not content.strip():
            raise ValueError("消息角色或内容无效")
        self.ensure_session(session_id)
        now = now_iso(); ids = list(dict.fromkeys(attachment_ids or []))
        with self.manager.connections.transaction() as conn:
            if ids:
                placeholders = ",".join("?" for _ in ids)
                rows = conn.execute(f"SELECT attachment_id FROM assistant_attachments WHERE session_id=? AND attachment_id IN ({placeholders})", [session_id, *ids]).fetchall()
                if {row[0] for row in rows} != set(ids): raise ValueError("附件不属于当前会话")
            cursor = conn.execute(
                "INSERT INTO assistant_messages(session_id,role,content,attachment_ids_json,metadata_json,created_at) VALUES(?,?,?,?,?,?)",
                (session_id, role, content, json.dumps(ids), json.dumps(metadata or {}, ensure_ascii=False), now),
            )
            message_id = int(cursor.lastrowid)
            if ids:
                conn.executemany("UPDATE assistant_attachments SET message_id=?,updated_at=? WHERE session_id=? AND attachment_id=?", [(message_id, now, session_id, item) for item in ids])
            conn.execute("UPDATE assistant_sessions SET updated_at=? WHERE session_id=?", (now, session_id))
        return message_id

    # Backward-compatible name used by existing callers.
    def add(self, session_id: str, role: str, content: str, metadata: dict | None = None) -> None:
        self.add_message(session_id, role, content, metadata=metadata)

    def create_attachment(self, data: dict[str, Any]) -> None:
        now = now_iso(); self.ensure_session(data["session_id"])
        columns = ("attachment_id","session_id","message_id","original_name","safe_name","media_type","extension","size_bytes","sha256","stored_path","parse_status","parse_error","parser_name","text_chars","paragraph_count","table_count","sheet_count","page_count","summary_json","parsed_json","created_at","updated_at")
        values = [data.get(name) for name in columns]
        values[18] = json.dumps(data.get("summary_json") or {}, ensure_ascii=False)
        values[19] = json.dumps(data.get("parsed_json") or {}, ensure_ascii=False)
        values[20] = values[20] or now; values[21] = values[21] or now
        with self.manager.connections.transaction() as conn:
            conn.execute(f"INSERT INTO assistant_attachments({','.join(columns)}) VALUES({','.join('?' for _ in columns)})", values)

    def update_attachment_parse(self, attachment_id: str, status: str, **values: Any) -> None:
        allowed = {"parse_error","parser_name","text_chars","paragraph_count","table_count","sheet_count","page_count","summary_json","parsed_json"}
        updates: dict[str, Any] = {"parse_status": status, "updated_at": now_iso()}
        updates.update({key: value for key, value in values.items() if key in allowed})
        for key in ("summary_json", "parsed_json"):
            if key in updates: updates[key] = json.dumps(updates[key], ensure_ascii=False)
        with self.manager.connections.transaction() as conn:
            cursor = conn.execute(f"UPDATE assistant_attachments SET {','.join(f'{key}=?' for key in updates)} WHERE attachment_id=?", [*updates.values(), attachment_id])
            if cursor.rowcount != 1: raise ValueError("附件记录不存在")

    def get_attachment(self, session_id: str, attachment_id: str) -> dict[str, Any]:
        with self.manager.connections.connection() as conn:
            row = conn.execute("SELECT * FROM assistant_attachments WHERE session_id=? AND attachment_id=?", (session_id, attachment_id)).fetchone()
        if row is None: raise ValueError("附件不存在或不属于当前会话")
        result = dict(row)
        for key in ("summary_json", "parsed_json"):
            try: result[key] = json.loads(result.get(key) or "{}")
            except json.JSONDecodeError: result[key] = {}
        return result

    def list_attachments(self, session_id: str, message_id: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM assistant_attachments WHERE session_id=?"; args: list[Any] = [session_id]
        if message_id is not None: query += " AND message_id=?"; args.append(message_id)
        query += " ORDER BY created_at"
        with self.manager.connections.connection() as conn: return [dict(row) for row in conn.execute(query, args)]

    def add_tool_run(self, session_id: str, message_id: int, tool_name: str, arguments: dict, status: str, result: dict | None = None, error: str = "") -> int:
        now = now_iso()
        with self.manager.connections.transaction() as conn:
            cursor = conn.execute("INSERT INTO assistant_tool_runs(session_id,message_id,tool_name,arguments_json,status,result_json,error,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?)", (session_id,message_id,tool_name,json.dumps(arguments,ensure_ascii=False),status,json.dumps(result or {},ensure_ascii=False),error,now,now))
            return int(cursor.lastrowid)

    def add_output(self, session_id: str, source_message_id: int, source_attachment_ids: list[str], tool_run_id: int, output: dict) -> int:
        with self.manager.connections.transaction() as conn:
            cursor = conn.execute("INSERT INTO assistant_outputs(session_id,source_message_id,source_attachment_ids_json,tool_run_id,file_name,file_type,file_path,size_bytes,validation_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (session_id,source_message_id,json.dumps(source_attachment_ids),tool_run_id,output["filename"],output["file_type"],output["absolute_path"],output["size"],output["validation"],now_iso()))
            return int(cursor.lastrowid)

    def list_outputs(self, session_id: str) -> list[dict]:
        with self.manager.connections.connection() as conn: return [dict(row) for row in conn.execute("SELECT * FROM assistant_outputs WHERE session_id=? ORDER BY output_id", (session_id,))]

    def page(self, session_id: str, limit: int = 50, before_id: int | None = None) -> list[dict]:
        limit=max(1,min(limit,100)); query="SELECT * FROM assistant_messages WHERE session_id=?"; args:[Any]=[session_id]
        if before_id is not None: query+=" AND message_id<?"; args.append(before_id)
        query+=" ORDER BY message_id DESC LIMIT ?"; args.append(limit)
        with self.manager.connections.connection() as conn: return [dict(row) for row in conn.execute(query,args).fetchall()][::-1]

    def context(self, session_id: str, budget: int = 12000, max_messages: int = 24) -> list[dict]:
        selected=[]; used=0
        for row in reversed(self.page(session_id,max_messages)):
            content=row["content"][-max(0,budget-used):]
            if content: selected.append({"role":row["role"],"content":content}); used+=len(content)
            if used>=budget: break
        return selected[::-1]
