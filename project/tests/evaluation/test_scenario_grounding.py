from pathlib import Path

from tests.evaluation.scenario_metrics import evaluate_records
from scripts.evaluate_scenario_pipeline import read_jsonl

GOLDEN = Path(__file__).parents[1] / "golden"


def report():
    return evaluate_records(
        read_jsonl(GOLDEN / "scenario_cases.jsonl"),
        read_jsonl(GOLDEN / "equipment_allocations.jsonl"),
    )


def test_all_required_golden_scenarios_are_present() -> None:
    rows = read_jsonl(GOLDEN / "scenario_cases.jsonl")
    assert len(rows) == 10
    assert {row["category"] for row in rows} == {
        "信息完整的正常场景", "缺少装备数量规则", "装备能力不满足", "库存不足",
        "项目资料与书籍参数冲突", "多个同名符号", "扫描PDF缺少内容",
        "跨项目检索尝试", "环境扰动场景", "故障降级与恢复场景",
    }


def test_grounding_and_safety_thresholds_are_strict() -> None:
    result = report()
    metrics = result["metrics"]
    assert metrics["scenario_field_completeness"] >= 0.95
    assert metrics["project_fact_source_coverage"] == 1.0
    assert metrics["unsupported_fact_rate"] == 0.0
    assert metrics["cross_project_leak_rate"] == 0.0
    assert metrics["blocking_issue_recall"] == 1.0
    assert metrics["book_override_rate"] == 0.0
    assert metrics["source_reference_validity"] == 1.0
    assert result["passed"]


def test_steps_stability_and_human_change_metrics() -> None:
    metrics = report()["metrics"]
    assert metrics["step_expected_result_alignment"] == 1.0
    assert metrics["repeat_run_stability"] == 1.0
    assert 0.0 < metrics["human_modified_field_ratio"] < 0.1
