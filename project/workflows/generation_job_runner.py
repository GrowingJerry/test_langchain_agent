"""Lease-owned execution of durable generation jobs."""
from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Any

from application.services.generation_job_service import GenerationJobService
from application.services.generation_service import GenerationRequest
from application.services.ui_application_service import UIApplicationService
from config.settings import Settings

logger = logging.getLogger("test_agent.generation.worker")


class GenerationJobRunner:
    def __init__(self, manager: Any, worker_id: str | None = None) -> None:
        self.manager = manager
        self.jobs = GenerationJobService(manager)
        self.worker_id = worker_id or f"{socket.gethostname()}-{__import__('os').getpid()}"

    def run_once(self) -> dict[str, Any] | None:
        job = self.jobs.claim_next(self.worker_id)
        if not job: return None
        job_id, token = job["job_id"], job["lease_token"]
        settings = Settings.model_validate(job["settings"])
        service = UIApplicationService(self.manager, None, settings)
        current = 0; stage = "starting"; message = ""; lease_lost = threading.Event(); done = threading.Event()

        def progress(event: dict[str, Any]) -> None:
            nonlocal current, stage, message
            current = max(current, int(event.get("index") or 1) - 1)
            stage=str(event.get("kind") or "generation"); message=str(event.get("content") or "")
            if not self.jobs.heartbeat(job_id, token, stage=stage, current=current, message=message,
                                       lease_seconds=settings.generation_job_lease_seconds):
                raise RuntimeError("generation_job_lease_lost")

        def lease_heartbeat() -> None:
            interval=max(5.0, settings.generation_job_lease_seconds/3)
            while not done.wait(interval):
                try:
                    if not self.jobs.heartbeat(job_id, token, stage=stage, current=current, message=message,
                                               lease_seconds=settings.generation_job_lease_seconds):
                        lease_lost.set(); return
                except Exception:
                    logger.exception("generation job heartbeat failed job_id=%s", job_id)

        heartbeat_thread=threading.Thread(target=lease_heartbeat, name=f"generation-heartbeat-{job_id}", daemon=True)
        heartbeat_thread.start()
        self.jobs.heartbeat(job_id, token, stage="worker_started", current=0,
            message=f"worker={self.worker_id} 已启动，正在构建生成上下文",
            lease_seconds=settings.generation_job_lease_seconds)

        try:
            result = service.generate_requirement_batch(
                GenerationRequest.model_validate(job["request"]), job["requirement_ids"], job["batch_id"],
                progress_callback=progress, force=bool(job["force_regenerate"]),
                cancellation_callback=lambda: lease_lost.is_set() or self.jobs.cancellation_requested(job_id, token))
            if result.get("failed"):
                error="; ".join(str(x.get("error") or x) for x in result["failed"])
                self.jobs.retry(job_id, token, error, settings.generation_job_retry_delay_seconds,
                                settings.generation_job_max_attempts)
                return {"job_id": job_id, "status": (self.jobs.get(job_id) or {}).get("status","retry_wait")}
            status = "cancelled" if result.get("cancelled") else (
                "needs_review" if result.get("needs_review") else "completed")
            self.jobs.finish(job_id, token, status, result=result)
            return {"job_id": job_id, "status": status}
        except Exception as exc:
            logger.exception("generation job failed job_id=%s", job_id)
            if self.jobs.cancellation_requested(job_id, token):
                self.jobs.finish(job_id, token, "cancelled", error=f"{type(exc).__name__}: {exc}")
                return {"job_id": job_id, "status": "cancelled"}
            self.jobs.retry(job_id, token, f"{type(exc).__name__}: {exc}",
                            settings.generation_job_retry_delay_seconds,
                            settings.generation_job_max_attempts)
            return {"job_id": job_id, "status": "retry_wait"}
        finally:
            done.set(); heartbeat_thread.join(timeout=2)
