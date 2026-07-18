"""Validate the local Python runtime and required imports without touching project data."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
from typing import List, Tuple

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PYTHON = (3, 10)
REQUIRED_IMPORTS: Tuple[Tuple[str, str], ...] = (
    ("streamlit", "streamlit"),
    ("numpy", "numpy"),
    ("pandas", "pandas"),
    ("openpyxl", "openpyxl"),
    ("pydantic", "pydantic"),
    ("requests", "requests"),
    ("dotenv", "python-dotenv"),
    ("docx", "python-docx"),
    ("fitz", "PyMuPDF"),
    ("langchain", "langchain"),
    ("langchain_ollama", "langchain-ollama"),
)


def _check_python() -> bool:
    """Return whether the interpreter is exactly Python 3.10 and print its version."""
    actual = sys.version_info[:2]
    valid = actual == EXPECTED_PYTHON
    status = "OK" if valid else "FAIL"
    print(f"[{status}] Python: {sys.version.split()[0]} (required: 3.10.x)")
    return valid


def _check_imports() -> List[str]:
    """Import required packages and return user-facing names that failed."""
    failures: List[str] = []
    for module_name, package_name in REQUIRED_IMPORTS:
        try:
            importlib.import_module(module_name)
        except (ImportError, RuntimeError, OSError) as exc:
            failures.append(package_name)
            print(f"[FAIL] Import {package_name}: {type(exc).__name__}: {exc}")
        else:
            print(f"[OK] Import {package_name}")
    return failures


def _print_safe_ollama_config() -> None:
    """Print only non-secret Ollama endpoint and model names."""
    load_dotenv(PROJECT_ROOT / ".env")
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "qwen3:8b")
    embed_model = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    vision_model = os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl:3b")
    print(f"Ollama URL: {base_url}")
    print(f"Ollama model: {model}")
    print(f"Ollama embedding model: {embed_model}")
    print(f"Ollama vision model: {vision_model}")


def main() -> int:
    """Run non-mutating environment checks and return a process exit code."""
    python_ok = _check_python()
    import_failures = _check_imports()
    _print_safe_ollama_config()
    if python_ok and not import_failures:
        print("Environment check: OK")
        return 0
    print("Environment check: FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
