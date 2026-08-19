from pathlib import Path
from application.services.csci_document_service import parse_formal_csci_docx
from application.services.project_service import ProjectManager
from application.services.ui_application_service import UIApplicationService
from config.settings import Settings

FIX=Path(__file__).parents[1]/'fixtures'/'convergence'

def test_shifted_csci_and_review_gate_share_generation_store(tmp_path):
    parsed=parse_formal_csci_docx(FIX/'shifted_csci.docx')
    assert parsed['boundaries']['overview']['number']=='4.2' and parsed['boundaries']['detail']['number']=='4.3'
    assert len(parsed['testable_nodes'])==1
    manager=ProjectManager(tmp_path/'db.sqlite'); project_id=manager.create_project('unified')['project_id']; service=UIApplicationService(manager,None,Settings(enable_ollama=False))
    result=service.extract_requirement_document(project_id,FIX/'shifted_csci.docx','auto'); assert result['extraction_method']=='csci_structured'
    assert not [x for x in manager.list_requirements(project_id) if x['retained']]
    rows=service.requirement_review_rows(project_id); rows[0]['enabled']=True; service.save_requirement_reviews(project_id,rows)
    submitted=service.submit_requirements_for_generation(project_id); assert submitted['submitted']==1
    assert len([x for x in manager.list_requirements(project_id) if x['retained']])==1

def test_binding_funnel_zero_is_explicit(tmp_path):
    manager=ProjectManager(tmp_path/'db.sqlite'); project_id=manager.create_project('empty')['project_id']; service=UIApplicationService(manager,None,Settings(enable_ollama=False))
    funnel=service.binding_funnel(project_id); assert funnel['最低可测功能']==0 and funnel['HTML页面']==0 and funnel['待绑定功能']==0
