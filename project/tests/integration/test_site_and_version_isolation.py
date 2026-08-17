from io import BytesIO
from zipfile import ZipFile
import pytest
from application.services.project_service import ProjectManager
from application.services.ui_application_service import UIApplicationService
from config.settings import Settings
from infrastructure.repositories.traceability_repository import TraceabilityRepository

def _zip():
    out=BytesIO()
    with ZipFile(out,"w") as zf: zf.writestr("index.html","<a href='page.html'>next</a>"); zf.writestr("page.html","<input id='name'>")
    return out.getvalue()

def test_site_package_and_version_actions_are_project_scoped(tmp_path):
    manager=ProjectManager(tmp_path/"workspace.db"); p1=manager.create_project("P1")["project_id"]; p2=manager.create_project("P2")["project_id"]
    service=UIApplicationService(manager,None,Settings(enable_ollama=False)); site=service.analyze_site_zip(p1,"site.zip",_zip())
    with manager.connections.connection() as conn:
        assert conn.execute("SELECT count(*) FROM html_pages WHERE project_id=? AND site_package_id=?",(p1,site["site_package_id"])).fetchone()[0]==2
        assert conn.execute("SELECT count(*) FROM html_pages WHERE project_id=?",(p2,)).fetchone()[0]==0
    manager.save_generated_case(p1,{"case_id":"TC-1","project_id":p1,"steps":["a"],"expected":["b"]})
    repo=TraceabilityRepository(manager.connections); repo.create_case_version(p1,"TC-1",{"case_id":"TC-1","project_id":p1,"steps":["a2"],"expected":["b2"]})
    with pytest.raises(KeyError): repo.accept_version(p2,"TC-1",1)
    repo.accept_version(p1,"TC-1",1); rolled=repo.rollback(p1,"TC-1",1)
    assert rolled["version_no"]==2 and len(repo.list_case_versions(p1,"TC-1"))==2


def test_csci_enhancement_publishes_to_normal_generation_requirements(tmp_path):
    """Structured parsing must feed the same store used by requirement generation."""
    from tests.fixtures.csci_demo.build_fixture import build

    manager = ProjectManager(tmp_path / "workspace.db")
    project_id = manager.create_project("CSCI统一流程")["project_id"]
    service = UIApplicationService(manager, None, Settings(enable_ollama=False))
    result = service.analyze_csci_docx(
        project_id, build(tmp_path / "synthetic-requirements.docx")
    )

    requirement_ids = {
        row["requirement_id"] for row in manager.list_requirements(project_id)
    }
    assert {node["identifier"] for node in result["nodes"]} <= requirement_ids
    profile = manager.get_requirement(project_id, "GRXXPZ")
    assert profile
    assert profile["section_path"] == ["新闻门户", "个人中心", "个人信息配置"]
    assert profile["inputs"] and profile["outputs"]
