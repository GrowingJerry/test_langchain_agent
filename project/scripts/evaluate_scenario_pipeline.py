"""Run the deterministic local golden evaluation and write JSON/Markdown reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.scenario_metrics import evaluate_records  # noqa: E402
from infrastructure.db.json_codec import dumps_json  # noqa: E402


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no}: JSONL record must be an object")
            rows.append(value)
    return rows


def markdown_report(report: Dict[str, Any]) -> str:
    lines = ["# Scenario Pipeline Evaluation", "", f"Overall: **{'PASS' if report['passed'] else 'FAIL'}**", "",
             "## Metrics", "", "| Metric | Value |", "|---|---:|"]
    lines.extend(f"| {name} | {value:.6f} |" for name, value in report["metrics"].items())
    lines.extend(["", "## Hard thresholds", "", "| Metric | Required | Result |",
                  "|---|---:|---|"])
    for name, expected in report["thresholds"].items():
        result = "PASS" if report["threshold_results"][name] else "FAIL"
        lines.append(f"| {name} | {expected:.6f} | {result} |")
    lines.extend(["", "## Golden cases", "", "| Case | Category | Expected blockers | Detected blockers |",
                  "|---|---|---|---|"])
    for case in report["cases"]:
        lines.append("| {case_id} | {category} | {expected} | {detected} |".format(
            case_id=case["case_id"], category=case["category"],
            expected=", ".join(case["expected_blocking_checks"]) or "-",
            detected=", ".join(case["detected_blocking_checks"]) or "-",
        ))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path,
                        default=PROJECT_ROOT / "tests/golden/scenario_cases.jsonl")
    parser.add_argument("--allocations", type=Path,
                        default=PROJECT_ROOT / "tests/golden/equipment_allocations.jsonl")
    parser.add_argument("--json-output", type=Path,
                        default=PROJECT_ROOT / "evaluation_report.json")
    parser.add_argument("--markdown-output", type=Path,
                        default=PROJECT_ROOT / "evaluation_report.md")
    args = parser.parse_args()
    report = evaluate_records(read_jsonl(args.scenarios), read_jsonl(args.allocations))
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(dumps_json(report, sort_keys=True) + "\n", encoding="utf-8")
    args.markdown_output.write_text(markdown_report(report), encoding="utf-8")
    print(dumps_json({"passed": report["passed"], "metrics": report["metrics"]}, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
