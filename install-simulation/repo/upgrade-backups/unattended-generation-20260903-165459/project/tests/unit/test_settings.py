"""Tests for validated environment settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from config.settings import Settings
import config.settings as settings_module


def test_settings_load_and_validate_environment(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {
            "OLLAMA_BASE_URL": "http://ollama.internal:11434/",
            "OLLAMA_MODEL": "text-model",
            "OLLAMA_EXTRACTION_MODEL": "extract-model",
            "OLLAMA_REVIEW_MODEL": "review-model",
            "OLLAMA_TIMEOUT": "45",
            "OLLAMA_NUM_CTX": "12288",
            "OLLAMA_STRUCTURED_NUM_PREDICT": "1024",
            "OLLAMA_MAX_RETRIES": "3",
            "ENABLE_OLLAMA": "false",
            "ENABLE_STREAMING": "true",
        },
        env_file=tmp_path / "missing.env",
    )
    assert settings.ollama_base_url == "http://ollama.internal:11434"
    assert settings.ollama_timeout == 45
    assert settings.ollama_num_ctx == 12288
    assert settings.ollama_structured_num_predict == 1024
    assert settings.ollama_max_retries == 3
    assert settings.enable_ollama is False
    assert settings.enable_streaming is True


def test_invalid_settings_have_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="OLLAMA_BASE_URL"):
        Settings.from_env(
            {"OLLAMA_BASE_URL": "localhost:11434"}, env_file=tmp_path / "none"
        )


def test_large_context_and_generation_aliases_are_supported(tmp_path: Path) -> None:
    configured = Settings.from_env(
        {
            "OLLAMA_MODEL": "qwen3.6:35b",
            "OLLAMA_AUDIT_MODEL": "qwen3.6:35b",
            "OLLAMA_VISION_MODEL": "qwen3-vl:8b",
            "OLLAMA_NUM_CTX": "262144",
            "OLLAMA_NUM_PREDICT": "32768",
            "OLLAMA_TIMEOUT": "900",
            "OLLAMA_MAX_RETRIES": "1",
        },
        env_file=tmp_path / "none",
    )
    assert configured.test_case_model == "qwen3.6:35b"
    assert configured.requirement_auditor_model == "qwen3.6:35b"
    assert configured.page_understanding_model == "qwen3-vl:8b"
    assert configured.ollama_num_ctx == 262144
    assert configured.ollama_structured_num_predict == 32768


def test_legacy_generation_max_seconds_maps_to_model_call_limit(tmp_path: Path) -> None:
    configured = Settings.from_env(
        {"GENERATION_MAX_SECONDS": "7200"}, env_file=tmp_path / "none"
    )
    assert configured.generation_max_seconds == 7200
    assert configured.effective_model_call_max_seconds == 7200


def test_new_model_call_limit_wins_over_legacy_value(tmp_path: Path) -> None:
    configured = Settings.from_env(
        {"GENERATION_MAX_SECONDS": "7200", "GENERATION_MODEL_CALL_MAX_SECONDS": "1800"},
        env_file=tmp_path / "none",
    )
    assert configured.effective_model_call_max_seconds == 1800


def test_upgrade_snapshot_preserves_site_model_but_not_obsolete_safety_values(tmp_path: Path, monkeypatch) -> None:
    config_dir=tmp_path/"config"; config_dir.mkdir()
    (config_dir/"preserved_runtime_settings.json").write_text(
        '{"test_case_model":"qwen3.6:35b","ollama_model":"qwen3.6:35b",'
        '"ollama_num_ctx":131072,"generation_max_cases_per_model_call":20}',
        encoding="utf-8")
    monkeypatch.setattr(settings_module,"PROJECT_ROOT",tmp_path)
    configured=Settings.from_env({},env_file=tmp_path/"missing.env")
    assert configured.test_case_model == "qwen3.6:35b"
    assert configured.ollama_num_ctx == 131072
    assert configured.generation_max_cases_per_model_call == 2
