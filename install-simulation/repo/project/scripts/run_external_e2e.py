"""Real Ollama + real Chromium acceptance for the fictional external fixture."""
from __future__ import annotations
import json,os,sys,time
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT)); os.environ["PLAYWRIGHT_BROWSERS_PATH"]=str(ROOT/"vendor"/"playwright-browsers")
from application.services.project_service import ProjectManager
from application.services.ui_application_service import UIApplicationService
from application.services.case_regeneration_service import CaseRegenerationService
from config.settings import Settings
from infrastructure.repositories.traceability_repository import TraceabilityRepository
from tests.fixtures.csci_demo.build_external_e2e_fixture import build_docx,build_site

def chat(settings,prompt,schema):
    response=requests.post(settings.ollama_base_url+"/api/chat",json={"model":settings.test_case_model,"stream":False,"think":False,"format":schema,"messages":[{"role":"user","content":prompt}],"options":{"temperature":0,"num_predict":3000,"num_ctx":settings.ollama_num_ctx}},timeout=settings.ollama_timeout); response.raise_for_status(); return json.loads(response.json()["message"]["content"])

def run()->dict:
    settings=Settings(text_model="qwen3:8b",vision_model="qwen2.5vl:3b",requirement_atomizer_model="qwen3:8b",requirement_auditor_model="qwen3:8b",page_understanding_model="qwen2.5vl:3b",test_case_model="qwen3:8b",test_case_review_model="qwen3:8b",ollama_model="qwen3:8b",ollama_vision_model="qwen2.5vl:3b",ollama_timeout=300)
    fixture=ROOT/"tests"/"fixtures"/"csci_demo"; docx=build_docx(fixture/"external_e2e_requirements.docx"); site_zip=build_site(fixture/"external_e2e_site.zip")
    db=ROOT/"outputs"/"e2e"/"external-e2e.db"; db.unlink(missing_ok=True); manager=ProjectManager(db); service=UIApplicationService(manager,None,settings); project_id=manager.create_project("外网真实模型E2E","虚构测试资料")["project_id"]
    service.ingest_document(project_id,docx.name,docx.read_bytes()); stored=next(x for x in service.document_summaries(project_id) if x["filename"]==docx.name); parsed=service.analyze_csci_docx(project_id,Path(stored["file_path"])); assert parsed["overview_nodes"] and parsed["testable_nodes"]
    atoms=service.atomize_and_audit_requirements(project_id); assert atoms["functions"]
    site=service.analyze_site_zip(project_id,site_zip.name,site_zip.read_bytes()); health=service.playwright_status(); assert health["available"]
    explored=service.auto_explore_site_package(project_id,site["site_package_id"],"index.html"); assert any(x.get("success") for x in explored["events"])
    binding=service.auto_bind_requirements(project_id); assert binding["bindings"] and binding["visual_understanding"].get("status")=="completed"
    rows=service.traceability_rows(project_id); indicator_ids=[x["indicator_id"] for x in rows["atomic_requirements"]]
    schema={"type":"object","required":["cases"],"properties":{"cases":{"type":"array","minItems":4,"items":{"type":"object","required":["case_id","title","objective","test_steps","expected_results","indicator_ids","expected_source","need_human_confirm"],"properties":{"case_id":{"type":"string"},"title":{"type":"string"},"objective":{"type":"string"},"test_steps":{"type":"array","items":{"type":"string"}},"expected_results":{"type":"array","items":{"type":"string"}},"indicator_ids":{"type":"array","items":{"type":"string"}},"expected_source":{"type":"string"},"need_human_confirm":{"type":"boolean"}}}}}}
    generated=chat(settings,"基于以下真实绑定证据生成公告查看、新增、标题为空、类型选择等正常异常边界用例。步骤与预期严格一一对应，不得编造数据库成功。只输出JSON："+json.dumps({"atoms":rows["atomic_requirements"],"bindings":binding["bindings"],"observations":explored["events"]},ensure_ascii=False),schema)["cases"]
    page_ids=list({x.get("page_id") for x in binding["bindings"] if x.get("page_id")}); element_ids=list({e for x in binding["bindings"] for e in x.get("element_ids",[])})
    with manager.connections.connection() as conn: observation_ids=[x[0] for x in conn.execute("SELECT observation_id FROM html_observations WHERE project_id=?",(project_id,))]
    for case in generated:
        pairs=min(len(case["test_steps"]),len(case["expected_results"])); case["test_steps"]=case["test_steps"][:pairs]; case["expected_results"]=case["expected_results"][:pairs]; case.update({"project_id":project_id,"requirement_ids":["ZH_TYMH_XWMH"],"function_id":"ZH_TYMH_XWMH","requirement_hierarchy_path":["统一门户","功能需求","新闻门户"],"page_ids":page_ids,"html_element_ids":element_ids,"playwright_observation_ids":observation_ids,"offline_verifiable":False,"online_verification_items":["真实数据库持久化"],"evaluation_criteria":"每步预期均可观察或明确待联机确认"}); manager.save_generated_case(project_id,case)
    first=generated[0]; regen=CaseRegenerationService(manager,settings).regenerate(project_id,first["case_id"],"只修改预期结果，不要编造数据库保存成功，明确标记待联机验证。"); repo=TraceabilityRepository(manager.connections); repo.accept_version(project_id,first["case_id"],regen["version"]["version_no"]); rollback=repo.rollback(project_id,first["case_id"],regen["version"]["version_no"])
    export=service.export_excel(project_id); assert export.exists()
    return {"project_id":project_id,"text_model":settings.text_model,"vision_model":settings.page_understanding_model,"nodes":len(parsed["nodes"]),"testable_nodes":len(parsed["testable_nodes"]),"atoms":len(indicator_ids),"pages":len(site["pages"]),"playwright_events":len(explored["events"]),"visual_status":binding["visual_understanding"].get("status"),"bindings":len(binding["bindings"]),"cases":len(generated),"rollback_version":rollback["version_no"],"export":str(export),"chromium":health.get("chromium_version")}

if __name__=="__main__": print(json.dumps(run(),ensure_ascii=False,indent=2))
