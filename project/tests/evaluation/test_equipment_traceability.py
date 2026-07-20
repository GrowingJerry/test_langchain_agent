from pathlib import Path

from evaluation.scenario_metrics import evaluate_records
from scripts.evaluate_scenario_pipeline import read_jsonl

GOLDEN = Path(__file__).parents[1] / "golden"


def test_equipment_and_quantity_traceability() -> None:
    result = evaluate_records(
        read_jsonl(GOLDEN / "scenario_cases.jsonl"),
        read_jsonl(GOLDEN / "equipment_allocations.jsonl"),
    )
    metrics = result["metrics"]
    assert metrics["equipment_role_coverage"] >= 0.7
    assert metrics["quantity_rule_traceability"] == 1.0
    assert metrics["ungrounded_quantity_fill_rate"] == 0.0
    assert metrics["source_reference_validity"] == 1.0


def test_missing_quantity_rule_stays_null_and_requires_confirmation() -> None:
    rows = read_jsonl(GOLDEN / "equipment_allocations.jsonl")
    allocation = next(row for row in rows if row["case_id"] == "missing-quantity-rule")["allocations"][0]
    assert allocation["quantity"] is None
    assert allocation["rule_id"] == ""
    assert allocation["need_human_confirm"] is True
