from pathlib import Path


def test_generation_page_uses_synchronous_streaming_without_task_polling() -> None:
    source = (Path(__file__).resolve().parents[2] / "ui" / "generation_page.py").read_text("utf-8")
    assert "generate_requirement_batch(" in source
    assert "progress_callback=relay" in source
    assert "GenerationTaskStore" not in source
    assert "submit_generation" not in source
    assert "request_cancel" not in source
    assert "cancellation_callback=" not in source
