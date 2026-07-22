from application.services.ui_state import begin_once, fail_once, finish_once, request_fingerprint


def test_request_fingerprint_is_stable_and_sensitive() -> None:
    assert request_fingerprint({"b": 2, "a": 1}) == request_fingerprint(
        {"a": 1, "b": 2}
    )
    assert request_fingerprint({"a": 1}) != request_fingerprint({"a": 2})


def test_generation_guard_prevents_rerun_duplicates_and_recovers_from_failure() -> None:
    state = {}
    fingerprint = request_fingerprint({"project_id": "P1"})
    assert begin_once(state, "generate", fingerprint)
    assert not begin_once(state, "generate", fingerprint)
    fail_once(state, "generate")
    assert begin_once(state, "generate", fingerprint)
    finish_once(state, "generate", fingerprint, {"ok": True})
    assert not begin_once(state, "generate", fingerprint)
    assert state["generate:result"] == {"ok": True}
