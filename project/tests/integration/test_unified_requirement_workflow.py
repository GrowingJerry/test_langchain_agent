from pathlib import Path
from application.services.csci_document_service import parse_formal_csci_docx
from application.services.project_service import ProjectManager
from application.services.ui_application_service import UIApplicationService
from config.settings import Settings
import pytest

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


def test_zero_confidence_unrelated_binding_cannot_be_confirmed(tmp_path):
    manager=ProjectManager(tmp_path/'db.sqlite'); project_id=manager.create_project('binding-guard')['project_id']; service=UIApplicationService(manager,None,Settings(enable_ollama=False))
    with manager.connections.transaction() as conn:
        conn.execute("INSERT INTO html_pages(project_id,page_id,title,page_path,source_asset) VALUES(?,?,?,?,?)",(project_id,'PAGE-PROFILE','个人信息配置','index.html','fixture.html'))
        conn.execute("INSERT INTO requirement_page_links(project_id,link_id,function_id,page_id,confidence,reason,status,need_human_confirm,model_name,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?)",(project_id,'LINK-1','NEWS','PAGE-PROFILE',0,'页面与新闻门户需求无关','unmatched',1,'fixture','{}'))
    with pytest.raises(ValueError,match='不能确认'):
        service.save_binding_reviews(project_id,[{'binding_type':'page','link_id':'LINK-1','page_id':'PAGE-PROFILE','confidence':0,'reason':'页面与新闻门户需求无关','confirmed':True}])
    with manager.connections.connection() as conn:
        row=conn.execute("SELECT status,need_human_confirm FROM requirement_page_links WHERE project_id=? AND link_id=?",(project_id,'LINK-1')).fetchone()
    assert row['status']=='unmatched' and row['need_human_confirm']==1


def test_model_declared_unrelated_page_is_persisted_as_unmatched(tmp_path, monkeypatch):
    manager=ProjectManager(tmp_path/'db.sqlite'); project_id=manager.create_project('unmatched')['project_id']; service=UIApplicationService(manager,None,Settings(enable_ollama=False))
    service.extract_requirement_document(project_id,FIX/'shifted_csci.docx','auto')
    service.analyze_offline_html(project_id,'profile.html','<html><title>个人信息配置</title><button>保存</button></html>'.encode(),include_elements=False)
    monkeypatch.setattr('application.services.semantic_binding_service._semantic_chat',lambda settings,schema,payload:{'page_id':payload['candidate_pages'][0]['page_id'],'confidence':0,'reason':'页面与新闻门户需求无关','element_ids':[],'need_human_confirm':True})
    result=service.auto_bind_requirements(project_id)
    assert result['confirmed']==0 and result['unmatched']==1
    with manager.connections.connection() as conn:
        row=conn.execute("SELECT page_id,status,need_human_confirm FROM requirement_page_links WHERE project_id=?",(project_id,)).fetchone()
    assert row['page_id']=='' and row['status']=='unmatched' and row['need_human_confirm']==1
