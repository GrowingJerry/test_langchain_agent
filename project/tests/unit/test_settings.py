"""Tests for validated environment settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from config.settings import Settings


def test_settings_load_and_validate_environment(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "OLLAMA_BASE_URL": "http://ollama.internal:11434/",
            "OLLAMA_MODEL": "text-model",
            "OLLAMA_EXTRACTION_MODEL": "extract-model",
            "OLLAMA_REVIEW_MODEL": "review-model",
            "OLLAMA_TIMEOUT": "45",
            "OLLAMA_MAX_RETRIES": "3",
            "ENABLE_OLLAMA": "false",
            "ENABLE_STREAMING": "true",
        },
        env_file=tmp_path / "missing.env",
    )
    assert settings.ollama_base_url == "http://ollama.internal:11434"
    assert settings.ollama_timeout == 45
    assert settings.ollama_max_retries == 3
    assert settings.enable_ollama is False
    assert settings.enable_streaming is True


def test_invalid_settings_have_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="OLLAMA_BASE_URL"):
        Settings.from_env(
            {"OLLAMA_BASE_URL": "localhost:11434"}, env_file=tmp_path / "none"
        )
