import pytest

from domain.case_schema import merge_case, normalize_case, validate_case


def _original():
    return {"case_id":"TC-1","project_id":"P1","requirement_ids":["R1"],"indicator_ids":["I1"],
        "test_purpose":"purpose","prerequisites":"ready","test_steps":["a","b"],
        "expected_result":["x","y"],"pass_criteria":"pass","provenance":{"nested":["source"]}}


def test_aliases_are_canonical_and_empty_values_do_not_erase_original():
    original=_original(); merged=merge_case(original,{"steps":[],"expected":["x2","y2"],"test_purpose":None})
    assert merged["test_steps"]==["a","b"] and merged["expected_result"]==["x2","y2"]
    assert "steps" not in merged and "expected" not in merged and merged["test_purpose"]=="purpose"


def test_expected_only_feedback_freezes_every_other_field_and_deep_copies():
    original=_original(); merged=merge_case(original,{"case_id":"evil","test_steps":["changed"],"expected":["x2","y2"],"case_name":"changed"},"只修改预期结果")
    assert merged["case_id"]=="TC-1" and merged["test_steps"]==["a","b"] and merged["expected_result"]==["x2","y2"]
    merged["provenance"]["nested"].append("mutation"); assert original["provenance"]=={"nested":["source"]}


def test_validation_rejects_empty_or_misaligned_steps():
    for revision in ({"test_steps":[],"expected_result":[]},{"test_steps":["a"],"expected_result":["x","y"]},{"test_steps":[""],"expected_result":["x"]}):
        with pytest.raises(ValueError): validate_case(revision)


def test_explicit_step_feedback_allows_aligned_growth():
    merged=merge_case(_original(),{"steps":["a","b","c"],"expected":["x","y","z"]},"增加一步点击取消按钮")
    assert len(validate_case(merged)["test_steps"])==3
