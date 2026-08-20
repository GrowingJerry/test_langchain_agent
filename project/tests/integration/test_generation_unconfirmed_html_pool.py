import json

from application.services.generation_package import build_generation_package
from application.services.generation_service import GenerationService
from application.services.project_service import ProjectManager
from config.settings import Settings
from domain.schemas.test_case import StructuredExpectedResult, StructuredTestStep, TestCase


def _context(project_id: str, page_links=None, element_links=None):
    return {
        "project_id": project_id,
        "requirement_id": "REQ-ABSTRACT",
        "requirement": {
            "requirement_id": "REQ-ABSTRACT", "title": "配置个人信息",
            "description": "用户输入姓名并保存个人资料", "inputs": ["姓名"],
            "processing_rules": ["校验后保存"], "outputs": ["保存结果"],
        },
        "traceability_context": {
            "atomic_requirements": [{"indicator_id": "ATOM-1", "indicator_text": "保存个人资料"}],
            "requirement_page_links": page_links or [],
            "requirement_element_links": element_links or [],
            "playwright_observations": [],
        },
    }


def _seed(manager: ProjectManager, project_id: str):
    with manager.connections.transaction() as conn:
        conn.execute("INSERT INTO html_pages(project_id,page_id,title,page_path,source_asset) VALUES(?,?,?,?,?)", (project_id,"PAGE-PROFILE","个人信息配置","profile.html","fixture.html"))
        conn.execute("INSERT INTO html_page_summaries(project_id,page_id,summary_json) VALUES(?,?,?)", (project_id,"PAGE-PROFILE",json.dumps({"visible_text_summary":"姓名 保存个人信息"},ensure_ascii=False)))
        conn.execute("INSERT INTO html_elements(project_id,element_id,page_id,tag,element_type,element_json) VALUES(?,?,?,?,?,?)", (project_id,"EL-SAVE","PAGE-PROFILE","button","button",json.dumps({"label":"保存个人信息","region":"右下角操作区","relative_position":"页面右下角","visible":True},ensure_ascii=False)))


def test_machine_unmatched_html_is_candidate_and_selected_ids_stay_unconfirmed(tmp_path):
    manager=ProjectManager(tmp_path/"db.sqlite"); project_id=manager.create_project("candidate")["project_id"]; _seed(manager,project_id)
    links=[{"page_id":"","status":"unmatched","confidence":0,"reason":"需求描述抽象，未找到相关元素","need_human_confirm":True}]
    wrapped=build_generation_package(manager,_context(project_id,links),num_ctx=32768,num_predict=8192,settings=Settings(enable_ollama=False))
    package=wrapped["package"]
    assert package["html_evidence_state"] == "machine_unmatched_candidate_pool"
    assert package["candidate_pool_stats"]["original_page_count"] == 1
    assert package["candidate_pool_stats"]["context_element_count"] == 1
    assert package["candidate_pages"][0]["business_elements"][0]["element_id"] == "EL-SAVE"
    assert package["candidate_pages"][0]["machine_binding"]["binding_source"] == "machine_unmatched"
    assert package["candidate_pages"][0]["machine_binding"]["status"] == "unmatched"
    case=TestCase(case_id="TEMP",title="保存资料",objective="验证保存",test_steps=["保存"],expected_results=["发起保存"],evaluation_criteria="可观察",requirement_ids=["REQ-ABSTRACT"],structured_steps=[StructuredTestStep(step_no=1,page_id="PAGE-PROFILE",element_id="EL-SAVE",action="click",instruction="点击保存",selection_reason="页面含保存个人信息按钮",expected_result=StructuredExpectedResult(element_change="保存操作被触发"))])
    result=GenerationService._validate_and_render_detailed_cases([case],[wrapped])[0]
    assert result.need_human_confirm is True
    assert result.page_ids == ["PAGE-PROFILE"] and result.html_element_ids == ["EL-SAVE"]
    assert result.structured_steps[0].binding_status == "model_selected_unconfirmed"


def test_human_rejected_page_is_excluded_and_no_html_is_requirement_only(tmp_path):
    manager=ProjectManager(tmp_path/"db.sqlite"); project_id=manager.create_project("rejected")["project_id"]; _seed(manager,project_id)
    rejected=build_generation_package(manager,_context(project_id,[{"page_id":"PAGE-PROFILE","status":"human_rejected"}]),num_ctx=32768,num_predict=8192,settings=Settings(enable_ollama=False))
    assert rejected["package"]["candidate_pages"] == []
    assert rejected["package"]["html_evidence_state"] == "machine_unmatched_candidate_pool"
    empty_id=manager.create_project("no-html")["project_id"]
    empty=build_generation_package(manager,_context(empty_id),num_ctx=32768,num_predict=8192,settings=Settings(enable_ollama=False))
    assert empty["package"]["html_evidence_state"] == "no_html_evidence"


def test_confirmed_element_has_priority_over_candidate_pool(tmp_path):
    manager=ProjectManager(tmp_path/"db.sqlite"); project_id=manager.create_project("confirmed")["project_id"]; _seed(manager,project_id)
    pages=[{"page_id":"PAGE-PROFILE","status":"confirmed","confidence":.95}]
    elements=[{"page_id":"PAGE-PROFILE","confirmed_element_id":"EL-SAVE","status":"confirmed","confidence":.95}]
    package=build_generation_package(manager,_context(project_id,pages,elements),num_ctx=32768,num_predict=8192,settings=Settings(enable_ollama=False))["package"]
    assert package["html_evidence_state"] == "confirmed_element"
    assert package["candidate_pages"] == []
    assert package["page_evidence"][0]["elements"][0]["element_id"] == "EL-SAVE"
