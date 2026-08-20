"""Classify Ollama HTTP failures, including non-retryable format grammar errors."""
from __future__ import annotations
from typing import Any

SCHEMA_ERROR_MARKERS=("error parsing grammar","failed to initialize grammar","failed to load model vocabulary required for format")

class NonRetryableSchemaError(RuntimeError):
    error_code="non_retryable_schema_error"

def response_error_text(response: Any, limit: int=8000) -> str:
    text=str(getattr(response,"text","") or "").strip()
    return text[:limit]+("…[truncated]" if len(text)>limit else "")

def raise_for_ollama_status(response: Any) -> None:
    if getattr(response,"ok",False): return
    detail=response_error_text(response)
    message=f"Ollama HTTP {getattr(response,'status_code','?')}: {detail}"
    if any(marker in detail.lower() for marker in SCHEMA_ERROR_MARKERS):
        raise NonRetryableSchemaError(f"non_retryable_schema_error: {message}")
    response.raise_for_status()
