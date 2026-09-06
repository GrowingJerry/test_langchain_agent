from application.services.coverage_generation import CaseDeduplicator, CoverageReconciler, GenerationBatchPlanner
from config.settings import Settings
from domain.schemas.coverage import TestPoint
from domain.schemas.test_case import TestCase


def point(atom: int, index: int) -> TestPoint:
    return TestPoint(test_point_id=f"TP-A{atom:02d}-{index:03d}", atomic_requirement_id=f"A{atom:02d}",
                     requirement_id="REQ-DEMO-001", title=f"场景{atom}-{index}", scenario_type="normal",
                     description="独立验证目的", priority="P1", evidence_requirement="requirement")


def case(tp: TestPoint, suffix: str = "") -> TestCase:
    return TestCase(case_id=f"TMP-{tp.test_point_id}{suffix}", title=tp.title, objective=tp.description,
                    test_steps=["执行明确操作" + suffix], expected_results=["观察明确结果" + suffix],
                    evaluation_criteria="实际结果符合预期", requirement_ids=[tp.requirement_id],
                    indicator_ids=[tp.atomic_requirement_id], atomic_requirement_id=tp.atomic_requirement_id,
                    test_point_id=tp.test_point_id, test_point_scenario_type=tp.scenario_type)


def test_one_atom_six_points_has_six_coverage_relations():
    points = [point(1, i) for i in range(1, 7)]
    audit = CoverageReconciler.audit(points, [case(x) for x in points])
    assert audit.coverage_status == "completed" and len(audit.generated_test_point_ids) == 6


def test_twenty_atoms_approximately_120_cases_are_not_requirement_capped():
    points = [point(atom, i) for atom in range(1, 21) for i in range(1, 7)]
    batches = GenerationBatchPlanner(Settings()).plan("REQ-DEMO-001", points, 65536)
    assert sum(len(x.test_points) for x in batches) == 120
    assert len(batches) == 60  # six points per atom, two points per safe model call
    assert max(len(batch.test_points) for batch in batches) == 2


def test_95_points_are_split_below_model_call_limit():
    points = [point(atom, i) for atom in range(1, 6) for i in range(1, 20)]
    batches = GenerationBatchPlanner(Settings(generation_max_cases_per_model_call=20)).plan("REQ", points, 65536)
    assert sum(map(lambda x: len(x.test_points), batches)) == 95
    assert len(batches) > 1 and max(len(x.test_points) for x in batches) <= 20


def test_missing_point_enters_repair_and_only_completed_after_repair():
    points = [point(1, i) for i in range(1, 11)]
    first = CoverageReconciler.audit(points, [case(x) for x in points[:-1]])
    assert first.missing_test_point_ids == [points[-1].test_point_id]
    repaired = CoverageReconciler.audit(points, [case(x) for x in points])
    assert repaired.coverage_status == "completed" and not repaired.missing_test_point_ids


def test_exhausted_repair_is_needs_review_not_completed():
    points = [point(1, i) for i in range(1, 4)]
    audit = CoverageReconciler.audit(points, [case(points[0])], repair_rounds_exhausted=True)
    assert audit.coverage_status == "needs_review" and len(audit.missing_test_point_ids) == 2


def test_duplicate_same_variant_keeps_more_complete_case_but_variants_survive():
    tp = point(1, 1)
    short = case(tp)
    long = short.model_copy(update={"case_id": "TMP-LONG", "test_steps": ["执行明确且包含数据的操作"]})
    # Same semantic purpose but genuinely different named data variant is retained.
    variant = short.model_copy(update={"case_id": "TMP-V", "data_variant": "空值"})
    deduped = CaseDeduplicator.deduplicate([short, long, variant])
    assert {x.case_id for x in deduped} == {"TMP-LONG", "TMP-V"}


def test_token_budget_shrinks_batch_instead_of_rejecting_requirement():
    points = [point(1, i) for i in range(1, 11)]
    settings = Settings(generation_expected_tokens_per_case=900, generation_expected_output_base_tokens=350)
    batches = GenerationBatchPlanner(settings).plan("REQ", points, available_output_tokens=2300)
    assert sum(len(x.test_points) for x in batches) == 10
    assert max(len(x.test_points) for x in batches) == 2


def test_demo_17_atoms_exceeds_100_and_has_unique_batch_point_ids():
    points = [point(atom, i) for atom in range(1, 18) for i in range(1, 7)]
    batches = GenerationBatchPlanner(Settings()).plan("REQ-DEMO-001", points, 65536)
    assert len(points) == 102
    assert len({x.test_point_id for batch in batches for x in batch.test_points}) == 102
