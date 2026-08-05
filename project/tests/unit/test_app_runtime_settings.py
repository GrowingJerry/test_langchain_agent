from pathlib import Path

import app
from application.container import ApplicationContainer
from config.settings import Settings


def test_runtime_config_is_applied_to_ui_service(tmp_path: Path) -> None:
    config = {
        "ollama_url": "http://localhost:11434",
        "model": "qwen3:8b",
        "use_ollama": True,
        "use_agent": True,
        "use_library": False,
        "top_k": 3,
    }
    runtime = app._settings_from_runtime_config(config)
    service = ApplicationContainer(Settings(enable_agent=False)).build_ui_service(
        tmp_path / "workspace.db",
        None,
        runtime,
    )
    assert service.settings.enable_agent is True
    assert service.settings.enable_ollama is True
    assert service.settings.ollama_model == "qwen3:8b"
