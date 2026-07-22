"""Metrics for fixed scenario/equipment golden records."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, List

from infrastructure.database.json_codec import dumps_json

REQUIRED_SCENARIO_FIELDS = (
    "scenario_id", "project_id", "title", "scenario_goal", "scenario_category",
    "simulation_object", "actors", "role_requirements", "preconditions", "normal_flow",
    "observed_variables", "success_criteria", "source_chunk_ids", "confidence",
)
FACT_FIELDS = (
    "initial_state", "preconditions", "trigger_events", "normal_flow", "abnormal_flows",
    "boundary_conditions", "recovery_flow", "environment_variables",
    "controllable_variables", "disturbance_variables", "observed_variables",
    "success_criteria", "failure_criteria",
)
PARAMETER_GROUPS = (
    "initial_state", "environment_variables", "controllable_variables",
    "disturbance_variables", "observed_variables",
)


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(dumps_json(value, sort_keys=True).encode("utf-8")).hexdigest()


def evaluate_records(
    scenarios: List[Dict[str, Any]], allocations: List[Dict[str, Any]]
) -> Dict[str, Any]:
    counters = {
        "required_fields": 0, "complete_fields": 0, "facts": 0, "sourced_facts": 0,
        "unsupported_facts": 0, "roles": 0, "covered_roles": 0,
        "quantities": 0, "traceable_quantities": 0, "untraceable_quantities": 0,
        "numeric_parameters": 0, "complete_parameters": 0,
        "foreign_refs": 0, "all_refs": 0, "expected_blockers": 0,
        "detected_blockers": 0, "book_conflicts": 0, "book_overrides": 0,
        "step_sets": 0, "aligned_step_sets": 0, "source_refs": 0,
        "valid_source_refs": 0, "generated_fields": 0, "modified_fields": 0,
        "stable_runs": 0, "repeat_runs": 0,
    }
    allocation_by_case = {row["case_id"]: row for row in allocations}
    details = []
    for record in scenarios:
        scenario = record["scenario"]
        provenance = record.get("provenance") or {}
        field_sources = provenance.get("field_sources") or {}
        catalog = record.get("source_catalog") or {}
        valid_chunks = set(catalog.get("chunk_ids") or [])
        valid_knowledge = set(catalog.get("knowledge_unit_ids") or [])
        valid_rules = set(catalog.get("rule_ids") or [])
        valid_jsonl_records = set(catalog.get("jsonl_record_nos") or [])
        counters["required_fields"] += len(REQUIRED_SCENARIO_FIELDS)
        counters["complete_fields"] += sum(_present(scenario.get(field)) for field in REQUIRED_SCENARIO_FIELDS)
        for field in FACT_FIELDS:
            if not _present(scenario.get(field)):
                continue
            counters["facts"] += 1
            if field in field_sources or scenario.get("source_chunk_ids") or scenario.get("knowledge_unit_ids"):
                counters["sourced_facts"] += 1
        counters["unsupported_facts"] += len(record.get("unsupported_facts") or [])
        for group in PARAMETER_GROUPS:
            for value in (scenario.get(group) or {}).values():
                if not isinstance(value, dict) or not isinstance(value.get("value"), (int, float)):
                    continue
                counters["numeric_parameters"] += 1
                if value.get("unit") and value.get("operating_condition") and _parameter_has_source(value, field_sources, group):
                    counters["complete_parameters"] += 1
        refs = _all_refs(scenario, provenance)
        for ref in refs:
            counters["all_refs"] += 1
            scope = ref.get("project_id")
            if scope and scope not in {scenario["project_id"], "GLOBAL"}:
                counters["foreign_refs"] += 1
            referenced = bool(
                (ref.get("chunk_id") and ref["chunk_id"] in valid_chunks)
                or (ref.get("knowledge_unit_id") and ref["knowledge_unit_id"] in valid_knowledge)
                or (ref.get("rule_id") and ref["rule_id"] in valid_rules)
                or (ref.get("jsonl_record_no") is not None
                    and ref["jsonl_record_no"] in valid_jsonl_records)
            )
            counters["source_refs"] += 1
            counters["valid_source_refs"] += int(referenced)
        expected = set(record.get("expected_blocking_checks") or [])
        detected = set(record.get("detected_blocking_checks") or [])
        counters["expected_blockers"] += len(expected)
        counters["detected_blockers"] += len(expected & detected)
        counters["book_conflicts"] += int(bool(record.get("book_project_conflict")))
        counters["book_overrides"] += int(bool(record.get("approved_project_fact_overridden")))
        steps = scenario.get("normal_flow") or []
        expected_results = record.get("expected_results") or []
        counters["step_sets"] += int(bool(steps or expected_results))
        counters["aligned_step_sets"] += int(bool(steps or expected_results) and len(steps) == len(expected_results))
        counters["generated_fields"] += int(record.get("generated_field_count") or 0)
        counters["modified_fields"] += len(record.get("human_modified_fields") or [])
        repeats = record.get("repeat_outputs") or []
        if repeats:
            counters["repeat_runs"] += 1
            counters["stable_runs"] += int(len({canonical_hash(item) for item in repeats}) == 1)
        allocation = allocation_by_case.get(record["case_id"], {})
        roles = {item["role_requirement_id"] for item in scenario.get("role_requirements") or []
                 if not item.get("optional")}
        allocated_roles = {item.get("role_requirement_id") for item in allocation.get("allocations") or []}
        counters["roles"] += len(roles)
        counters["covered_roles"] += len(roles & allocated_roles)
        for item in allocation.get("allocations") or []:
            for ref in item.get("source_refs") or []:
                counters["all_refs"] += 1
                scope = ref.get("project_id")
                if scope and scope not in {scenario["project_id"], "GLOBAL"}:
                    counters["foreign_refs"] += 1
                valid = bool(
                    (ref.get("rule_id") and ref["rule_id"] in valid_rules)
                    or (ref.get("jsonl_record_no") is not None
                        and ref["jsonl_record_no"] in valid_jsonl_records)
                    or (ref.get("chunk_id") and ref["chunk_id"] in valid_chunks)
                )
                counters["source_refs"] += 1
                counters["valid_source_refs"] += int(valid)
            quantity = item.get("quantity")
            if quantity is None:
                continue
            counters["quantities"] += 1
            traceable = bool(item.get("rule_id") or item.get("quantity_source") == "user")
            counters["traceable_quantities"] += int(traceable)
            counters["untraceable_quantities"] += int(not traceable)
        details.append({"case_id": record["case_id"], "category": record["category"],
                        "expected_blocking_checks": sorted(expected),
                        "detected_blocking_checks": sorted(detected)})
    metrics = {
        "scenario_field_completeness": _ratio(counters["complete_fields"], counters["required_fields"]),
        "project_fact_source_coverage": _ratio(counters["sourced_facts"], counters["facts"]),
        "unsupported_fact_rate": _ratio(counters["unsupported_facts"], counters["facts"]),
        "equipment_role_coverage": _ratio(counters["covered_roles"], counters["roles"]),
        "quantity_rule_traceability": _ratio(counters["traceable_quantities"], counters["quantities"]),
        "ungrounded_quantity_fill_rate": _ratio(counters["untraceable_quantities"], counters["quantities"]),
        "parameter_unit_condition_completeness": _ratio(counters["complete_parameters"], counters["numeric_parameters"]),
        "cross_project_leak_rate": _ratio(counters["foreign_refs"], counters["all_refs"]),
        "blocking_issue_recall": _ratio(counters["detected_blockers"], counters["expected_blockers"]),
        "book_override_rate": _ratio(counters["book_overrides"], counters["book_conflicts"]),
        "step_expected_result_alignment": _ratio(counters["aligned_step_sets"], counters["step_sets"]),
        "repeat_run_stability": _ratio(counters["stable_runs"], counters["repeat_runs"]),
        "human_modified_field_ratio": _ratio(counters["modified_fields"], counters["generated_fields"]),
        "source_reference_validity": _ratio(counters["valid_source_refs"], counters["source_refs"]),
    }
    thresholds = {
        "cross_project_leak_rate": 0.0,
        "ungrounded_quantity_fill_rate": 0.0,
        "book_override_rate": 0.0,
        "source_reference_validity": 1.0,
    }
    threshold_results = {
        name: (metrics[name] == expected) for name, expected in thresholds.items()
    }
    return {"metrics": metrics, "thresholds": thresholds,
            "threshold_results": threshold_results,
            "passed": all(threshold_results.values()), "counters": counters, "cases": details}


def _present(value: Any) -> int:
    return int(value is not None and value != "" and value != [] and value != {})


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 1.0


def _parameter_has_source(value: Dict[str, Any], field_sources: Dict[str, Any], group: str) -> bool:
    return bool(value.get("source_ref") or value.get("source_chunk_id")
                or value.get("knowledge_unit_id")
                or field_sources.get(f"{group}.{value.get('name', '')}"))


def _all_refs(scenario: Dict[str, Any], provenance: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for chunk_id in scenario.get("source_chunk_ids") or []:
        yield {"chunk_id": chunk_id, "project_id": scenario["project_id"]}
    for unit_id in scenario.get("knowledge_unit_ids") or []:
        yield {"knowledge_unit_id": unit_id, "project_id": scenario["project_id"]}
    for item in scenario.get("equipment_allocations") or []:
        yield from item.get("source_refs") or []
    for refs in (provenance.get("field_sources") or {}).values():
        if not isinstance(refs, dict):
            continue
        if refs.get("chunk_id"):
            yield {"chunk_id": refs["chunk_id"], "project_id": scenario["project_id"]}
        for chunk_id in refs.get("chunk_ids") or []:
            yield {"chunk_id": chunk_id, "project_id": scenario["project_id"]}
        if refs.get("knowledge_unit_id"):
            yield {"knowledge_unit_id": refs["knowledge_unit_id"],
                   "project_id": scenario["project_id"]}
