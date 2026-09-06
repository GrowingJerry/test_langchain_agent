from datetime import datetime, timedelta, timezone

from application.services.generation_job_service import GenerationJobService
from application.services.generation_service import GenerationRequest
from application.services.project_service import ProjectManager
from config.settings import Settings
from workflows.generation_job_runner import GenerationJobRunner


def make_job(tmp_path):
    manager=ProjectManager(tmp_path/"jobs.db")
    project_id=manager.create_project("unattended")["project_id"]
    jobs=GenerationJobService(manager)
    request=GenerationRequest(project_id=project_id,requirement_ids=["REQ-1"],case_count=4)
    job=jobs.enqueue(project_id,"BATCH-1",request.model_dump(mode="json"),["REQ-1"],
                     Settings(enable_ollama=False).model_dump(mode="json"))
    return manager,project_id,jobs,job


def test_enqueue_is_idempotent_for_active_batch(tmp_path):
    manager,project_id,jobs,job=make_job(tmp_path)
    duplicate=jobs.enqueue(project_id,"BATCH-1",job["request"],["REQ-1"],job["settings"])
    assert duplicate["job_id"] == job["job_id"]
    assert jobs.latest(project_id)[0]["status"] == "queued"


def test_expired_worker_lease_is_reclaimed(tmp_path):
    manager,_,jobs,job=make_job(tmp_path)
    first=jobs.claim_next("worker-a",lease_seconds=60)
    with manager.connections.transaction() as conn:
        conn.execute("UPDATE generation_jobs SET lease_expires_at=? WHERE job_id=?",
                     ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),job["job_id"]))
    second=jobs.claim_next("worker-b",lease_seconds=60)
    assert second and second["lease_token"] != first["lease_token"]
    assert not jobs.heartbeat(job["job_id"],first["lease_token"],stage="stale",current=0)


def test_cancel_is_persistent_and_not_claimed_again(tmp_path):
    _,project_id,jobs,job=make_job(tmp_path)
    claimed=jobs.claim_next("worker")
    assert jobs.request_cancel(project_id,job["job_id"])
    assert jobs.cancellation_requested(job["job_id"],claimed["lease_token"])
    assert jobs.finish(job["job_id"],claimed["lease_token"],"cancelled")
    assert jobs.claim_next("other") is None


def test_retry_wait_preserves_request_and_checkpoint_identity(tmp_path):
    _,_,jobs,job=make_job(tmp_path)
    claimed=jobs.claim_next("worker")
    assert jobs.retry(job["job_id"],claimed["lease_token"],"temporary ollama error",0,20)
    resumed=jobs.claim_next("worker-2")
    assert resumed["batch_id"] == "BATCH-1"
    assert resumed["request"]["requirement_ids"] == ["REQ-1"]
    assert resumed["attempt_count"] == 2


def test_worker_completes_durable_job_without_ui_session(tmp_path, monkeypatch):
    manager,_,jobs,job=make_job(tmp_path)
    class FakeUI:
        def __init__(self, manager, library, settings): pass
        def generate_requirement_batch(self, request, requirement_ids, batch_id, **kwargs):
            kwargs["progress_callback"]({"kind":"progress","index":1,"content":"model token activity"})
            return {"completed":requirement_ids,"failed":[],"needs_review":[],"cases":[]}
    monkeypatch.setattr("workflows.generation_job_runner.UIApplicationService",FakeUI)
    result=GenerationJobRunner(manager,"worker-test").run_once()
    persisted=jobs.get(job["job_id"])
    assert result["status"] == "completed"
    assert persisted["status"] == "completed"
    assert persisted["progress_current"] == persisted["progress_total"] == 1


def test_worker_retries_failed_requirement_and_keeps_durable_job(tmp_path, monkeypatch):
    manager,_,jobs,job=make_job(tmp_path)
    class FailedUI:
        def __init__(self, manager, library, settings): pass
        def generate_requirement_batch(self, *args, **kwargs):
            return {"completed":[],"failed":[{"requirement_id":"REQ-1","error":"temporary connection reset"}],
                    "needs_review":[],"cases":[]}
    monkeypatch.setattr("workflows.generation_job_runner.UIApplicationService",FailedUI)
    result=GenerationJobRunner(manager,"worker-test").run_once()
    persisted=jobs.get(job["job_id"])
    assert result["status"] == "retry_wait"
    assert persisted["status"] == "retry_wait"
    assert "connection reset" in persisted["last_error"]
