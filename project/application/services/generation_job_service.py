"""Durable unattended test-case generation jobs."""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from infrastructure.database.json_codec import dumps_json, loads_json
from infrastructure.repositories.base import new_id


TERMINAL = {"completed", "cancelled", "failed", "needs_review"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GenerationJobService:
    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def enqueue(self, project_id: str, batch_id: str, request: dict[str, Any],
                requirement_ids: list[str], settings: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
        now = utc_now(); job_id = new_id("GJOB")
        with self.manager.connections.transaction() as conn:
            existing = conn.execute("""SELECT * FROM generation_jobs WHERE project_id=? AND batch_id=?
                AND status IN ('queued','running','retry_wait') ORDER BY created_at DESC LIMIT 1""",
                (project_id, batch_id)).fetchone()
            if existing:
                return self._decode(dict(existing))
            conn.execute("""INSERT INTO generation_jobs(
                job_id,project_id,batch_id,status,request_json,requirement_ids_json,settings_json,
                force_regenerate,progress_current,progress_total,stage,created_at,updated_at,next_attempt_at
                ) VALUES(?,?,?,'queued',?,?,?,?,0,?,'queued',?,?,?)""",
                (job_id, project_id, batch_id, dumps_json(request), dumps_json(requirement_ids),
                 dumps_json(settings), int(force), len(requirement_ids), now, now, now))
            self._event(conn, job_id, "JOB_QUEUED", {"batch_id": batch_id, "requirements": requirement_ids})
        return self.get(job_id) or {}

    def claim_next(self, worker_id: str, lease_seconds: int = 300) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc); now_s = now.isoformat(); token = secrets.token_hex(16)
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.manager.connections.transaction() as conn:
            row = conn.execute("""SELECT job_id FROM generation_jobs
                WHERE status IN ('queued','running','retry_wait') AND cancel_requested=0
                AND next_attempt_at<=? AND (lease_expires_at IS NULL OR lease_expires_at<?)
                ORDER BY created_at LIMIT 1""", (now_s, now_s)).fetchone()
            if not row: return None
            changed = conn.execute("""UPDATE generation_jobs SET status='running',stage='worker_claimed',
                last_message='后台 worker 已领取任务，正在载入项目上下文',worker_id=?,lease_token=?,
                lease_expires_at=?,last_heartbeat_at=?,attempt_count=attempt_count+1,
                started_at=COALESCE(started_at,?),updated_at=?
                WHERE job_id=? AND (lease_expires_at IS NULL OR lease_expires_at<?)""",
                (worker_id, token, expires, now_s, now_s, now_s, row["job_id"], now_s)).rowcount
            if not changed: return None
            self._event(conn, row["job_id"], "JOB_CLAIMED", {"worker_id": worker_id, "lease_expires_at": expires})
        return self.get(row["job_id"])

    def heartbeat(self, job_id: str, token: str, *, stage: str, current: int,
                  message: str = "", lease_seconds: int = 300) -> bool:
        now = datetime.now(timezone.utc); expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.manager.connections.transaction() as conn:
            changed = conn.execute("""UPDATE generation_jobs SET lease_expires_at=?,last_heartbeat_at=?,
                updated_at=?,stage=?,progress_current=?,last_message=? WHERE job_id=? AND lease_token=?
                AND status='running'""", (expires, now.isoformat(), now.isoformat(), stage, current,
                message[-4000:], job_id, token)).rowcount
        return bool(changed)

    def cancellation_requested(self, job_id: str, token: str) -> bool:
        with self.manager.connections.connection() as conn:
            row = conn.execute("SELECT cancel_requested,lease_token FROM generation_jobs WHERE job_id=?", (job_id,)).fetchone()
        return not row or row["lease_token"] != token or bool(row["cancel_requested"])

    def finish(self, job_id: str, token: str, status: str, result: dict[str, Any] | None = None,
               error: str = "") -> bool:
        now = utc_now()
        with self.manager.connections.transaction() as conn:
            changed = conn.execute("""UPDATE generation_jobs SET status=?,result_json=?,last_error=?,
                progress_current=CASE WHEN ?='completed' THEN progress_total ELSE progress_current END,
                lease_token=NULL,lease_expires_at=NULL,finished_at=?,updated_at=? WHERE job_id=? AND lease_token=?""",
                (status, dumps_json(result or {}), error[-12000:], status, now, now, job_id, token)).rowcount
            if changed: self._event(conn, job_id, "JOB_" + status.upper(), {"error": error})
        return bool(changed)

    def retry(self, job_id: str, token: str, error: str, delay_seconds: int, max_attempts: int) -> bool:
        job = self.get(job_id) or {}
        if int(job.get("attempt_count") or 0) >= max_attempts:
            return self.finish(job_id, token, "failed", error=error)
        next_at = (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat()
        with self.manager.connections.transaction() as conn:
            changed = conn.execute("""UPDATE generation_jobs SET status='retry_wait',last_error=?,next_attempt_at=?,
                lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE job_id=? AND lease_token=?""",
                (error[-12000:], next_at, utc_now(), job_id, token)).rowcount
            if changed: self._event(conn, job_id, "JOB_RETRY_SCHEDULED", {"next_attempt_at": next_at, "error": error})
        return bool(changed)

    def request_cancel(self, project_id: str, job_id: str) -> bool:
        with self.manager.connections.transaction() as conn:
            changed = conn.execute("UPDATE generation_jobs SET cancel_requested=1,updated_at=? WHERE project_id=? AND job_id=? AND status NOT IN ('completed','cancelled','failed','needs_review')",
                (utc_now(), project_id, job_id)).rowcount
            if changed: self._event(conn, job_id, "CANCEL_REQUESTED", {})
        return bool(changed)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.manager.connections.connection() as conn:
            row = conn.execute("SELECT * FROM generation_jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._decode(dict(row)) if row else None

    def latest(self, project_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self.manager.connections.connection() as conn:
            rows = conn.execute("SELECT * FROM generation_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, limit)).fetchall()
        return [self._decode(dict(x)) for x in rows]

    @staticmethod
    def _event(conn: Any, job_id: str, event_type: str, details: dict[str, Any]) -> None:
        conn.execute("""INSERT INTO generation_job_events(project_id,job_id,event_type,details_json,created_at)
            SELECT project_id,?,?,?,? FROM generation_jobs WHERE job_id=?""",
            (job_id, event_type, dumps_json(details), utc_now(), job_id))

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        for key in ("request_json", "requirement_ids_json", "settings_json", "result_json"):
            row[key.removesuffix("_json")] = loads_json(row.get(key), [] if key == "requirement_ids_json" else {})
        return row
