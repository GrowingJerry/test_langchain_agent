from pathlib import Path


def test_generation_page_submits_durable_background_job() -> None:
    source = (Path(__file__).resolve().parents[2] / "ui" / "generation_page.py").read_text("utf-8")
    assert "enqueue_generation_job(" in source
    assert "generation_job_history(" in source
    assert "cancel_generation_job(" in source
    assert "generate_requirement_batch(" not in source
    assert "progress_callback=relay" not in source
    assert "GenerationTaskStore" not in source
    assert "submit_generation" not in source
    assert "cancellation_callback=" not in source
