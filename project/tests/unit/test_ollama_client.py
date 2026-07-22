"""Tests for bounded local Ollama response parsing."""

from infrastructure.llm.ollama_client import OllamaClient


def test_extract_json_accepts_exact_json_and_json_fence() -> None:
    client = OllamaClient()

    assert client.extract_json('{"ok": true}') == {"ok": True}
    assert client.extract_json('```json\n{"ok": true}\n```') == {"ok": True}


def test_extract_json_rejects_surrounding_prose() -> None:
    client = OllamaClient()

    assert client.extract_json('result: {"ok": true}') is None
