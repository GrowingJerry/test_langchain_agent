"""Real qwen3:8b acceptance through the durable worker process."""
from __future__ import annotations
import json, subprocess, sys, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from application.services.generation_job_service import GenerationJobService
from application.services.generation_service import GenerationRequest
from application.services.project_service import ProjectManager
from config.settings import Settings
from infrastructure.documents.ingestor import save_and_ingest_document

def main() -> int:
    root=ROOT/"outputs"/"unattended-ollama"/str(int(time.time())); root.mkdir(parents=True,exist_ok=True)
    db=root/"workspace.db"; manager=ProjectManager(db); project_id=manager.create_project("无人值守真实验收")["project_id"]
    save_and_ingest_document(manager,project_id,"requirements.txt","系统应接受有效订单并对空订单给出明确错误。".encode("utf-8"))
    chunk=manager.list_chunks(project_id,1)[0]; manager.save_profile(project_id,{"project_name":"无人值守真实验收","test_object":"订单系统"})
    manager.replace_requirements(project_id,[{"requirement_id":"REQ-1","title":"订单接收","description":"系统应接受有效订单并对空订单给出明确错误。","source_document":"requirements.txt","source_chunk_id":chunk["chunk_id"]}])
    with manager.connections.transaction() as conn:
        conn.execute("INSERT INTO requirement_indicators(project_id,indicator_id,capability_id,function_id,parent_indicator_id,indicator_text,indicator_type,source_json,rules_json,verification_scope,need_human_confirm) VALUES(?,?,?,?,?,?,?,?,?,?,0)",
                     (project_id,"ATOM-ORDER","REQ-1","REQ-1","","接受有效订单并拒绝空订单","normal",json.dumps({"source_text":"系统应接受有效订单并对空订单给出明确错误。"},ensure_ascii=False),"{}","offline_verifiable"))
    settings=Settings(enable_ollama=True,enable_agent=True,test_case_model="qwen3:8b",ollama_model="qwen3:8b",
        text_model="qwen3:8b",generation_max_cases_per_model_call=2,generation_default_max_cases_per_atom=4,
        ollama_structured_num_predict=4096,ollama_timeout=300,generation_idle_timeout_seconds=300,
        generation_job_retry_delay_seconds=1,generation_job_max_attempts=3)
    request=GenerationRequest(project_id=project_id,requirement_ids=["REQ-1"],case_count=4,max_cases_per_atom=4,
                              auto_case_count=True,requested_mode="auto",use_history=False)
    jobs=GenerationJobService(manager); job=jobs.enqueue(project_id,"REAL-UNATTENDED",request.model_dump(mode="json"),["REQ-1"],settings.model_dump(mode="json"))
    worker=ROOT/"scripts"/"run_generation_worker.py"
    completed=subprocess.run([sys.executable,str(worker),"--db",str(db),"--once"],cwd=str(ROOT),timeout=1800)
    final=jobs.get(job["job_id"]); cases=manager.list_generated_cases(project_id)
    report={"worker_returncode":completed.returncode,"job":final,"case_count":len(cases),"database":str(db)}
    (root/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),"utf-8")
    print(json.dumps({"status":final["status"],"case_count":len(cases),"database":str(db)},ensure_ascii=False))
    return 0 if final["status"] in {"completed","needs_review"} and cases else 1

if __name__=="__main__": raise SystemExit(main())
