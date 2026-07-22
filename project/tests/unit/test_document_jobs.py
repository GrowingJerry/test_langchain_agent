from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import application.services.project_service as manager_module
from application.services.project_service import ProjectManager
from workflows.learning.job_service import DocumentJobService


@pytest.fixture
def jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    manager = ProjectManager(tmp_path / "jobs.db")
    project_id = manager.create_project("long document jobs")["project_id"]
    source = manager.uploads_dir(project_id) / "book.pdf"
    source.write_bytes(b"pdf")
    document_id = manager.add_document(
        project_id, source.name, "pdf", source, "job-test-hash", "pymupdf"
    )
    return manager, DocumentJobService(manager), project_id, document_id


def test_create_job_is_idempotent(jobs) -> None:
    _, service, project_id, document_id = jobs
    first = service.create_job(project_id, document_id, 200)
    second = service.create_job(project_id, document_id, 200)
    assert first["job_id"] == second["job_id"]
    assert len(service.list_jobs(project_id)) == 1


def test_concurrent_workers_only_claim_job_once(jobs) -> None:
    _, service, project_id, document_id = jobs
    service.create_job(project_id, document_id, 200)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(service.claim_next, ("worker-a", "worker-b")))
    claimed = [item for item in claims if item is not None]
    assert len(claimed) == 1
    assert claimed[0]["lease_token"]


def test_failed_job_retries_then_stays_failed(jobs) -> None:
    _, service, project_id, document_id = jobs
    service.create_job(project_id, document_id, 200)
    for expected_retry in range(1, 4):
        claimed = service.claim_next(f"worker-{expected_retry}")
        assert claimed is not None
        assert service.fail(claimed["job_id"], claimed["lease_token"], "boom", max_retries=2)
        current = service.get_job(claimed["job_id"])
        assert current["retry_count"] == expected_retry
    assert current["status"] == "failed"
    assert service.claim_next("late-worker") is None


def test_expired_lease_resumes_from_committed_page(jobs) -> None:
    manager, service, project_id, document_id = jobs
    service.create_job(project_id, document_id, 200)
    first = service.claim_next("crashed-worker", lease_seconds=120)
    assert first is not None
    assert service.heartbeat(
        first["job_id"], first["lease_token"], current_page=40, total_pages=200
    )
    with manager.connections.transaction() as conn:
        conn.execute(
            "UPDATE document_processing_jobs SET lease_expires_at=? WHERE job_id=?",
            ("2000-01-01T00:00:00+00:00", first["job_id"]),
        )
    resumed = service.claim_next("replacement-worker")
    assert resumed is not None
    assert resumed["job_id"] == first["job_id"]
    assert resumed["current_page"] == 40
    assert resumed["lease_token"] != first["lease_token"]


def test_pause_resume_and_cancel_are_project_scoped(jobs) -> None:
    _, service, project_id, document_id = jobs
    job = service.create_job(project_id, document_id, 200)
    assert not service.pause("OTHER", job["job_id"])
    assert service.pause(project_id, job["job_id"])
    assert service.claim_next("worker") is None
    assert service.resume(project_id, job["job_id"])
    claimed = service.claim_next("worker")
    assert claimed is not None
    assert service.cancel(project_id, job["job_id"])
    assert service.control_state(job["job_id"], claimed["lease_token"]) == "cancelled"
