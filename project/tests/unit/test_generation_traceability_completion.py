from application.services.generation_service import GenerationRequest, GenerationService
from domain.schemas.test_case import TestCase


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
