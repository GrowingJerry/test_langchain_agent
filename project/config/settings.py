# -*- coding: utf-8 -*-
"""Validated application settings loaded once from environment and ``.env``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, field_validator


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseModel):
    """Small validated settings object without an additional settings framework."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:8b"
    ollama_extraction_model: str = "qwen3:8b"
    ollama_review_model: str = "qwen3:8b"
    ollama_embed_model: str = "nomic-embed-text"
    ollama_vision_model: str = "qwen2.5vl:3b"
    text_model: str = "qwen3:8b"
    vision_model: str = "qwen2.5vl:3b"
    requirement_atomizer_model: str = "qwen3:8b"
    requirement_auditor_model: str = "qwen3:8b"
    page_understanding_model: str = "qwen2.5vl:3b"
    test_case_model: str = "qwen3:8b"
    test_case_review_model: str = "qwen3:8b"
    ollama_timeout: int = Field(default=120, ge=1, le=3600)
    ollama_num_ctx: int = Field(default=8192, ge=2048, le=131072)
    ollama_structured_num_predict: int = Field(default=2048, ge=256, le=16384)
    ollama_max_retries: int = Field(default=2, ge=0, le=10)
    ollama_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    ollama_extraction_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    ollama_review_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    ollama_vision_timeout: int = Field(default=300, ge=1, le=3600)
    ollama_vision_image_max_side: int = Field(default=896, ge=28, le=8192)
    enable_ollama: bool = True
    enable_case_library: bool = True
    top_k_cases: int = Field(default=5, ge=1, le=100)
    enable_agent: bool = False
    agent_max_model_calls: int = Field(default=6, ge=1, le=100)
    agent_max_tool_calls: int = Field(default=12, ge=1, le=500)
    agent_model_max_retries: int = Field(default=2, ge=0, le=10)
    agent_tool_max_retries: int = Field(default=1, ge=0, le=10)
    structured_output_max_retries: int = Field(default=2, ge=0, le=10)
    enable_streaming: bool = False
    upload_max_bytes: int = Field(default=20 * 1024 * 1024, ge=1, le=200 * 1024 * 1024)
    document_max_chars: int = Field(default=2_000_000, ge=1000, le=20_000_000)
    pdf_max_pages: int = Field(default=500, ge=1, le=5000)
    background_document_page_threshold: int = Field(default=50, ge=1, le=5000)
    background_document_bytes_threshold: int = Field(
        default=5 * 1024 * 1024, ge=1024, le=200 * 1024 * 1024
    )
    document_ocr_enabled: bool = True
    document_ocr_engine: str = "rapidocr"
    document_ocr_dpi: int = Field(default=220, ge=96, le=400)
    document_ocr_min_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    log_level: str = "INFO"
    langsmith_tracing: bool = False
    data_dir: Path = PROJECT_ROOT / "data"
    outputs_dir: Path = PROJECT_ROOT / "outputs"

    @field_validator("ollama_base_url")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("OLLAMA_BASE_URL must start with http:// or https://")
        return normalized

    @field_validator("ollama_model", "ollama_extraction_model", "ollama_review_model", "ollama_vision_model", "text_model", "vision_model", "requirement_atomizer_model", "requirement_auditor_model", "page_understanding_model", "test_case_model", "test_case_review_model")
    @classmethod
    def _require_model_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Ollama model name must not be empty")
        return normalized

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(
                "LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL"
            )
        return normalized

    @classmethod
    def from_env(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        env_file: Optional[Path] = None,
    ) -> "Settings":
        """Load settings from an optional dotenv file and an environment mapping."""
        source: Dict[str, Any] = {}
        dotenv_path = env_file if env_file is not None else PROJECT_ROOT / ".env"
        if dotenv_path.is_file():
            source.update(
                {
                    key: value
                    for key, value in dotenv_values(dotenv_path).items()
                    if value is not None
                }
            )
        source.update(dict(os.environ if environ is None else environ))
        field_values = {
            field_name: source[env_name]
            for field_name in cls.model_fields
            if (env_name := field_name.upper()) in source
        }
        legacy = source.get("OLLAMA_MODEL")
        if legacy:
            for field_name in ("text_model", "requirement_atomizer_model", "requirement_auditor_model", "test_case_model", "test_case_review_model"):
                if field_name.upper() not in source:
                    field_values[field_name] = legacy
        legacy_vision = source.get("OLLAMA_VISION_MODEL")
        if legacy_vision:
            for field_name in ("vision_model", "page_understanding_model"):
                if field_name.upper() not in source:
                    field_values[field_name] = legacy_vision
        return cls.model_validate(field_values)

    @property
    def output_sqlite_dir(self) -> Path:
        return self.outputs_dir / "sqlite"

    @property
    def default_case_library_db(self) -> Path:
        return self.output_sqlite_dir / "case_library.db"


settings = Settings.from_env()

# Stable configuration exports used by the application.
OLLAMA_BASE_URL = settings.ollama_base_url
OLLAMA_MODEL = settings.ollama_model
OLLAMA_EXTRACTION_MODEL = settings.ollama_extraction_model
OLLAMA_REVIEW_MODEL = settings.ollama_review_model
OLLAMA_EMBED_MODEL = settings.ollama_embed_model
OLLAMA_VISION_MODEL = settings.ollama_vision_model
OLLAMA_TIMEOUT = settings.ollama_timeout
OLLAMA_NUM_CTX = settings.ollama_num_ctx
OLLAMA_STRUCTURED_NUM_PREDICT = settings.ollama_structured_num_predict
OLLAMA_MAX_RETRIES = settings.ollama_max_retries
OLLAMA_VISION_TIMEOUT = settings.ollama_vision_timeout
OLLAMA_VISION_IMAGE_MAX_SIDE = settings.ollama_vision_image_max_side
ENABLE_OLLAMA = settings.enable_ollama
ENABLE_CASE_LIBRARY = settings.enable_case_library
TOP_K_CASES = settings.top_k_cases
DATA_DIR = settings.data_dir
OUTPUTS_DIR = settings.outputs_dir
OUTPUT_SQLITE_DIR = settings.output_sqlite_dir
DEFAULT_CASE_LIBRARY_DB = settings.default_case_library_db

# The default databases need a parent directory before first use. Other output
# directories are created by the feature that owns them.
OUTPUT_SQLITE_DIR.mkdir(parents=True, exist_ok=True)
