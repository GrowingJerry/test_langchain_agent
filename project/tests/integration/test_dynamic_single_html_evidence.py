from pathlib import Path

import pytest

import application.services.project_service as manager_module
from application.services.project_service import ProjectManager
from application.services.ui_application_service import UIApplicationService
from config.settings import Settings


@pytest.mark.playwright
@pytest.mark.parametrize("acceptance_round",[1,2,3])
def test_single_dynamic_html_uses_site_pipeline_and_formal_page_id(tmp_path,monkeypatch,acceptance_round):
    monkeypatch.setattr(manager_module,"PROJECT_OUTPUT_ROOT",tmp_path/"projects")
    manager=ProjectManager(tmp_path/"workspace.db")
    project_id=manager.create_project(f"dynamic-{acceptance_round}") ["project_id"]
    service=UIApplicationService(manager,None,Settings(enable_ollama=False,playwright_render_timeout_seconds=5))
    fixture=Path(__file__).parents[1]/"fixtures"/"dynamic_html"/"spa.html"
    first=service.analyze_offline_html(project_id,"original-spa.html",fixture.read_bytes(),include_elements=False)
    second=service.analyze_offline_html(project_id,"original-spa.html",fixture.read_bytes(),include_elements=False)
    assert first["page_type"]=="dynamic_spa" and first["source_element_count"]==0
    assert first["rendered_element_count"]>0 and second["site_package_id"]==first["site_package_id"]
    with manager.connections.connection() as conn:
        page=conn.execute("SELECT page_id,page_path FROM html_pages WHERE project_id=?",(project_id,)).fetchone()
        observation=conn.execute("SELECT page_id,action FROM html_observations WHERE project_id=?",(project_id,)).fetchone()
        elements=conn.execute("SELECT count(*) FROM html_elements WHERE project_id=? AND page_id=?",(project_id,page["page_id"])).fetchone()[0]
    assert page["page_id"].startswith("PAGE-") and page["page_path"]=="index.html"
    assert observation["page_id"]==page["page_id"] and observation["action"]=="browser_rendered"
    assert elements==second["rendered_element_count"]
