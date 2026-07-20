"""Execution boundary for one leased long-document job."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from core.document_ingestor import DocumentIngestionInterrupted, save_and_ingest_document
from learning.job_service import DocumentJobService


class JobControlInterrupt(DocumentIngestionInterrupted):
    def __init__(self, state: str) -> None:
        super().__init__(state)
        self.state = state


class DocumentJobRunner:
    def __init__(self, manager: Any, max_retries: int = 3) -> None:
        self.manager = manager
        self.jobs = DocumentJobService(manager)
        self.max_retries = max_retries

    def run_claimed(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job_id = str(job["job_id"])
        token = str(job["lease_token"])
        document = next(
            (row for row in self.manager.list_documents(str(job["project_id"]))
             if row["document_id"] == job["document_id"]),
            None,
        )
        if not document:
            self.jobs.fail(job_id, token, "document not found", self.max_retries)
            return self.jobs.get_job(job_id) or {}

        def checkpoint(report: Dict[str, Any]) -> None:
            state = self.jobs.control_state(job_id, token)
            if state in {"paused", "cancelled", "lease_lost"}:
                raise JobControlInterrupt(state)
            if not self.jobs.heartbeat(
                job_id,
                token,
                current_page=int(report.get("last_completed_page") or 0),
                total_pages=int(report.get("total_pages") or 0),
                stage="parsing",
            ):
                raise JobControlInterrupt("lease_lost")

        try:
            path = Path(str(document["file_path"]))
            result = save_and_ingest_document(
                self.manager,
                str(job["project_id"]),
                str(document["filename"]),
                path.read_bytes(),
                progress_callback=checkpoint,
            )
            if not self.jobs.heartbeat(
                job_id, token,
                current_page=int(result.get("parse_report", {}).get("last_completed_page") or 0),
                total_pages=int(result.get("parse_report", {}).get("total_pages") or 0),
                stage="indexing",
            ):
                raise JobControlInterrupt("lease_lost")
            self.jobs.finish(job_id, token)
        except JobControlInterrupt as exc:
            if exc.state in {"paused", "cancelled"}:
                self.jobs.release_controlled(job_id, token, exc.state)
        except Exception as exc:
            self.jobs.fail(job_id, token, f"{type(exc).__name__}: {exc}", self.max_retries)
        return self.jobs.get_job(job_id) or {}

    def run_once(self, worker_id: str) -> Dict[str, Any] | None:
        job = self.jobs.claim_next(worker_id)
        return self.run_claimed(job) if job else None
