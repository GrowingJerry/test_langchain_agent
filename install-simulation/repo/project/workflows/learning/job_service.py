"""SQLite-backed document job queue with atomic leases."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from infrastructure.repositories.base import new_id, now_iso

ACTIVE_STATUSES = ("pending", "parsing", "indexing", "extracting", "paused")
TERMINAL_STATUSES = ("completed", "cancelled")


class DocumentJobService:
    def __init__(self, manager: Any) -> None:
        self.manager = manager
        self.connections = manager.connections

    def create_job(self, project_id: str, document_id: str, total_pages: int = 0) -> Dict[str, Any]:
        """Idempotently return the active or completed job for a document."""
        with self.connections.transaction() as conn:
            row = conn.execute(
                """SELECT * FROM document_processing_jobs
                WHERE project_id=? AND document_id=? ORDER BY created_at DESC LIMIT 1""",
                (project_id, document_id),
            ).fetchone()
            if row:
                return dict(row)
            timestamp = now_iso()
            job_id = new_id("JOB")
            conn.execute(
                """INSERT INTO document_processing_jobs(
                job_id,project_id,document_id,status,current_page,total_pages,stage,
                progress,error_message,retry_count,created_at,updated_at,idempotency_key
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, project_id, document_id, "pending", 0, max(total_pages, 0),
                 "queued", 0.0, "", 0, timestamp, timestamp,
                 f"{project_id}:{document_id}"),
            )
            return dict(conn.execute("SELECT * FROM document_processing_jobs WHERE job_id=?", (job_id,)).fetchone())

    def get_job(self, job_id: str, project_id: str = "") -> Optional[Dict[str, Any]]:
        query = "SELECT * FROM document_processing_jobs WHERE job_id=?"
        params: tuple[Any, ...] = (job_id,)
        if project_id:
            query += " AND project_id=?"
            params += (project_id,)
        with self.connections.connection() as conn:
            row = conn.execute(query, params).fetchone()
            return dict(row) if row else None

    def list_jobs(self, project_id: str) -> List[Dict[str, Any]]:
        with self.connections.connection() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM document_processing_jobs WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            )]

    def claim_next(self, worker_id: str, lease_seconds: int = 120) -> Optional[Dict[str, Any]]:
        """Atomically lease one runnable job; expired leases are reclaimable."""
        now = datetime.now(timezone.utc)
        now_value = now.isoformat()
        expires = (now + timedelta(seconds=lease_seconds)).isoformat()
        token = uuid4().hex
        with self.connections.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """SELECT job_id FROM document_processing_jobs
                    WHERE status IN ('pending','parsing','indexing','extracting')
                    AND (lease_expires_at IS NULL OR lease_expires_at < ?)
                    ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,
                    created_at LIMIT 1""",
                    (now_value,),
                ).fetchone()
                if not row:
                    conn.commit()
                    return None
                changed = conn.execute(
                    """UPDATE document_processing_jobs SET
                    lease_owner=?,lease_token=?,lease_expires_at=?,
                    status=CASE WHEN status='pending' THEN 'parsing' ELSE status END,
                    stage=CASE WHEN status='pending' THEN 'parsing' ELSE stage END,
                    updated_at=? WHERE job_id=?
                    AND (lease_expires_at IS NULL OR lease_expires_at < ?)""",
                    (worker_id, token, expires, now_value, row["job_id"], now_value),
                ).rowcount
                if changed != 1:
                    conn.rollback()
                    return None
                claimed = conn.execute(
                    "SELECT * FROM document_processing_jobs WHERE job_id=?",
                    (row["job_id"],),
                ).fetchone()
                conn.commit()
                return dict(claimed)
            except Exception:
                conn.rollback()
                raise

    def heartbeat(self, job_id: str, lease_token: str, *, current_page: int,
                  total_pages: int, stage: str = "parsing", lease_seconds: int = 120) -> bool:
        now = datetime.now(timezone.utc)
        progress = min(1.0, current_page / total_pages) if total_pages else 0.0
        status = stage if stage in {"parsing", "indexing", "extracting"} else "parsing"
        with self.connections.transaction() as conn:
            return conn.execute(
                """UPDATE document_processing_jobs SET current_page=?,total_pages=?,stage=?,status=?,
                progress=?,lease_expires_at=?,updated_at=? WHERE job_id=? AND lease_token=?
                AND status IN ('parsing','indexing','extracting')""",
                (max(current_page, 0), max(total_pages, 0), stage, status, progress,
                 (now + timedelta(seconds=lease_seconds)).isoformat(), now.isoformat(), job_id, lease_token),
            ).rowcount == 1

    def control_state(self, job_id: str, lease_token: str) -> str:
        with self.connections.connection() as conn:
            row = conn.execute("SELECT status,cancel_requested,lease_token FROM document_processing_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row or row["lease_token"] != lease_token:
            return "lease_lost"
        if row["cancel_requested"] or row["status"] == "cancelled":
            return "cancelled"
        return str(row["status"])

    def pause(self, project_id: str, job_id: str) -> bool:
        return self._control(project_id, job_id, "paused", cancel=False)

    def cancel(self, project_id: str, job_id: str) -> bool:
        return self._control(project_id, job_id, "cancelled", cancel=True)

    def resume(self, project_id: str, job_id: str) -> bool:
        with self.connections.transaction() as conn:
            return conn.execute(
                """UPDATE document_processing_jobs SET status='pending',stage='queued',
                lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,cancel_requested=0,updated_at=?
                WHERE project_id=? AND job_id=? AND status IN ('paused','failed')""",
                (now_iso(), project_id, job_id),
            ).rowcount == 1

    def _control(self, project_id: str, job_id: str, status: str, cancel: bool) -> bool:
        with self.connections.transaction() as conn:
            return conn.execute(
                """UPDATE document_processing_jobs SET status=?,cancel_requested=?,updated_at=?
                WHERE project_id=? AND job_id=? AND status NOT IN ('completed','cancelled')""",
                (status, int(cancel), now_iso(), project_id, job_id),
            ).rowcount == 1

    def finish(self, job_id: str, lease_token: str) -> bool:
        return self._finish(job_id, lease_token, "completed", "completed", "")

    def fail(self, job_id: str, lease_token: str, error: str, max_retries: int = 3) -> bool:
        job = self.get_job(job_id)
        retry_count = int((job or {}).get("retry_count") or 0) + 1
        status = "pending" if retry_count <= max_retries else "failed"
        with self.connections.transaction() as conn:
            return conn.execute(
                """UPDATE document_processing_jobs SET status=?,stage='failed',error_message=?,
                retry_count=?,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=?
                WHERE job_id=? AND lease_token=?""",
                (status, error[:4000], retry_count, now_iso(), job_id, lease_token),
            ).rowcount == 1

    def release_controlled(self, job_id: str, lease_token: str, status: str) -> bool:
        with self.connections.transaction() as conn:
            return conn.execute(
                """UPDATE document_processing_jobs SET status=?,lease_owner=NULL,lease_token=NULL,
                lease_expires_at=NULL,updated_at=? WHERE job_id=? AND lease_token=?""",
                (status, now_iso(), job_id, lease_token),
            ).rowcount == 1

    def _finish(self, job_id: str, token: str, status: str, stage: str, error: str) -> bool:
        with self.connections.transaction() as conn:
            return conn.execute(
                """UPDATE document_processing_jobs SET status=?,stage=?,progress=1.0,error_message=?,
                lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=?
                WHERE job_id=? AND lease_token=?""",
                (status, stage, error, now_iso(), job_id, token),
            ).rowcount == 1
