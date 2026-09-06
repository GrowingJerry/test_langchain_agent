import subprocess
import sys
from pathlib import Path

from application.services.generation_job_service import GenerationJobService
from application.services.generation_service import GenerationRequest
from application.services.project_service import ProjectManager
from config.settings import Settings


def test_separate_worker_process_completes_after_submitter_exits(tmp_path: Path):
    db=tmp_path/"worker.db"; manager=ProjectManager(db)
    project_id=manager.create_project("overnight-process")["project_id"]
    manager.upsert_requirement(project_id,{"requirement_id":"REQ-1","title":"查询状态",
        "description":"用户可以查询当前状态，并在无记录时看到明确提示。","source_document":"fixture.txt"})
    request=GenerationRequest(project_id=project_id,requirement_ids=["REQ-1"],case_count=2,
                              generation_strategy="legacy",requested_mode="rule")
    jobs=GenerationJobService(manager)
    job=jobs.enqueue(project_id,"PROCESS-BATCH",request.model_dump(mode="json"),["REQ-1"],
                     Settings(enable_ollama=False,enable_agent=False).model_dump(mode="json"))
    script=Path(__file__).resolve().parents[2]/"scripts"/"run_generation_worker.py"
    completed=subprocess.run([sys.executable,str(script),"--db",str(db),"--once"],
                             cwd=str(script.parent.parent),capture_output=True,text=True,timeout=60)
    assert completed.returncode == 0, completed.stdout+completed.stderr
    reopened=ProjectManager(db); persisted=GenerationJobService(reopened).get(job["job_id"])
    assert persisted["status"] == "completed"
    assert reopened.list_generated_cases(project_id)
