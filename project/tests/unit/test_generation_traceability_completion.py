from application.services.generation_service import GenerationRequest, GenerationService
from domain.schemas.test_case import TestCase
from domain.schemas.test_case import StructuredExpectedResult, StructuredTestStep


def _case(case_id: str) -> TestCase:
    return TestCase(
        case_id=case_id,
        title=case_id,
        objective="验证功能",
        test_steps=["执行操作"],
        expected_results=["显示结果"],
        evaluation_criteria="结果可观察",
        requirement_ids=["REQ-1"],
    )


def test_fallback_cases_receive_complete_project_scoped_traceability() -> None:
    cases = [_case("TC-1"), _case("TC-2")]
    context = {
        "requirement_id": "REQ-1",
        "traceability_context": {
            "atomic_requirements": [
                {"indicator_id": "ATOM-1"},
                {"indicator_id": "ATOM-2"},
                {"indicator_id": "ATOM-3"},
            ],
            "requirement_page_links": [
                {"page_id": "PAGE-1", "status": "confirmed"},
                {"page_id": "PAGE-X", "status": "rejected"},
            ],
            "requirement_element_links": [
                {"confirmed_element_id": "EL-1", "status": "confirmed"}
            ],
            "playwright_observations": [
                {"observation_id": "OBS-1", "page_id": "PAGE-1"},
                {"observation_id": "OBS-X", "page_id": "PAGE-X"},
            ],
        },
    }
    request = GenerationRequest(project_id="P-1", requirement_ids=["REQ-1"])

    GenerationService._complete_traceability(
        cases, {"REQ-1": context}, [context], request
    )

    assert {item for case in cases for item in case.indicator_ids} == {
        "ATOM-1",
        "ATOM-2",
        "ATOM-3",
    }
    assert all(case.page_ids == ["PAGE-1"] for case in cases)
    assert all(case.html_element_ids == ["EL-1"] for case in cases)
    assert all(case.playwright_observation_ids == ["OBS-1"] for case in cases)


def test_structured_step_is_rendered_from_confirmed_html_evidence() -> None:
    case = _case("TC-DETAILED").model_copy(update={"structured_steps": [
        StructuredTestStep(
            step_no=1,
            element_id="EL-ADD",
            action="click",
            instruction="点击新增",
            expected_result=StructuredExpectedResult(page_change="页面中央打开新增对话框"),
        )
    ]})
    packages = [{"package": {"page_evidence": [{
        "page_id": "PAGE-LIST", "title": "公告列表", "binding_status": "confirmed",
        "elements": [{"element_id": "EL-ADD", "label": "新增公告", "element_type": "button", "region": "右上角操作区", "binding_status": "confirmed"}],
    }]}}]

    rendered = GenerationService._validate_and_render_detailed_cases([case], packages)[0]

    assert rendered.test_steps == ["在〖公告列表〗页面的右上角操作区，点击〖新增公告〗。"]
    assert rendered.expected_results == ["页面中央打开新增对话框"]


def test_pure_requirement_clears_invented_ui_and_marks_confirmation() -> None:
    case = _case("TC-PURE").model_copy(update={"structured_steps": [
        StructuredTestStep(
            step_no=1, page_name="模型编造页面", region="右上角", element_name="〖保存〗",
            action="click", instruction="执行需求规定的保存操作",
            expected_result=StructuredExpectedResult(online_confirmation="保存结果待联机确认"),
        )
    ]})
    packages = [{"package": {"requirement": {"description": "保存配置"}, "page_evidence": [],
        "generation_requirements": {"evidence_policy": {"html_level": "strong", "include_elements": True}}}}]

    rendered = GenerationService._validate_and_render_detailed_cases([case], packages, "功能测试")[0]

    assert rendered.structured_steps[0].page_name == ""
    assert rendered.structured_steps[0].element_name == ""
    assert rendered.need_human_confirm is True
    assert any("没有已确认HTML页面证据" in item for item in rendered.missing_information)


def test_interface_case_does_not_require_html_region() -> None:
    case = _case("TC-API").model_copy(update={"structured_steps": [
        StructuredTestStep(
            step_no=1, action="observe", instruction="调用接口并记录响应",
            expected_result=StructuredExpectedResult(data_change="返回HTTP响应码和响应体"),
        )
    ]})
    packages = [{"package": {"requirement": {"description": "查询接口"}, "page_evidence": [],
        "generation_requirements": {"evidence_policy": {"html_level": "off", "include_elements": False}}}}]

    rendered = GenerationService._validate_and_render_detailed_cases([case], packages, "接口测试")[0]

    assert rendered.test_steps == ["调用接口并记录响应"]
    assert rendered.expected_results == ["返回HTTP响应码和响应体"]


def test_confirmed_element_requires_real_region_evidence() -> None:
    case = _case("TC-NO-REGION").model_copy(update={"structured_steps": [
        StructuredTestStep(step_no=1, element_id="EL-1", action="click", instruction="点击保存",
            expected_result=StructuredExpectedResult(element_change="按钮触发保存"))
    ]})
    packages = [{"package": {"page_evidence": [{"page_id":"PAGE-1", "title":"配置", "binding_status":"confirmed",
        "elements":[{"element_id":"EL-1", "label":"保存", "binding_status":"confirmed"}]}]}}]

    import pytest
    with pytest.raises(Exception, match="真实区域"):
        GenerationService._validate_and_render_detailed_cases([case], packages, "功能测试")
