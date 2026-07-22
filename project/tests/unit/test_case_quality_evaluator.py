from domain.rules.case_quality import evaluate_case_quality


def test_persistence_identifiers_are_not_treated_as_numeric_facts() -> None:
    case = {
        "case_id": "TC-001",
        "generation_run_id": "RUN-5636e2415394",
        "scenario_id": "SCN-123456",
        "source_chunk_ids": ["CHK-987654"],
        "test_steps": ["记录状态", "执行操作", "检查结果"],
        "input_data": ["项目规定输入"],
        "expected_result": ["完成"],
        "pass_criteria": "结果可观察",
    }
    context = {
        "requirement": {"description": "验证结果可观察"},
        "related_scenario_cards": [],
        "related_chunks": [{"chunk_id": "CHK-987654", "content": "项目规定输入"}],
    }

    quality = evaluate_case_quality(case, context)

    assert quality["has_fabricated_metrics"] is False
    assert not any("5636" in issue for issue in quality["issues"])
