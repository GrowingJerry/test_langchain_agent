"""Real ordered stability acceptance: DOCX -> Ollama -> 2.9MB HTML -> binding -> Playwright."""
from __future__ import annotations
import json
import tempfile
import tracemalloc
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from application.services.project_service import ProjectManager
from application.services.ui_application_service import UIApplicationService
from config.settings import settings

def large_html()->bytes:
    row='<label for="field-{0}">字段{0}</label><input id="field-{0}" name="field-{0}" value="{1}">'
    body="".join(row.format(i,"x"*100) for i in range(18000))
    data=("<!doctype html><html><head><meta charset='utf-8'><title>个人信息配置</title><link rel='stylesheet' href='app.css'></head><body><form>"+body+"<button id='save'>保存</button></form><script src='app.js'></script></body></html>").encode("utf-8")
    assert len(data)>=2_900_000
    return data

def main()->int:
    work=Path(tempfile.mkdtemp(prefix="stability-e2e-")); manager=ProjectManager(work/"workspace.db")
    project_id=manager.create_project("内网稳定性真实验收")["project_id"]; service=UIApplicationService(manager,None,settings)
    docx=ROOT/"tests/fixtures/csci_demo/external_e2e_requirements.docx"
    parsed=service.analyze_csci_docx(project_id,docx); assert parsed["testable_count"]>0
    events=[]; atoms=service.atomize_and_audit_requirements(project_id,lambda x:events.append(x),resume=True); assert atoms["functions"] and not atoms["failures"]
    tracemalloc.start(); html=service.analyze_offline_html(project_id,"large-profile.html",large_html(),include_elements=False); _,peak=tracemalloc.get_traced_memory(); tracemalloc.stop()
    assert html["element_count"]>10000 and "elements" not in html
    page=service.list_html_elements_page(project_id,1,100); assert len(page["rows"])<=100
    binding=service.auto_bind_requirements(project_id); assert binding["bindings"]
    zip_path=ROOT/"tests/fixtures/csci_demo/external_e2e_site.zip"; site=service.analyze_site_zip(project_id,zip_path.name,zip_path.read_bytes())
    health=service.playwright_status(); assert health["available"]
    explored=service.auto_explore_site_package(project_id,site["site_package_id"],"index.html"); assert explored["return_code"]==0 and explored["events"]
    report={"project_id":project_id,"text_model":settings.text_model,"testable_count":parsed["testable_count"],"atom_count":sum(x.get("atom_count",0) for x in atoms["functions"]),"atom_progress_events":len(events),"html_bytes":len(large_html()),"html_elements":html["element_count"],"browser_rows":len(page["rows"]),"html_peak_memory_bytes":peak,"bindings":len(binding["bindings"]),"playwright_events":len(explored["events"]),"playwright_return_code":explored["return_code"],"playwright_log":explored["playwright_log"],"chromium":health.get("chromium_version")}
    print(json.dumps(report,ensure_ascii=False,indent=2)); return 0

if __name__=="__main__": raise SystemExit(main())
