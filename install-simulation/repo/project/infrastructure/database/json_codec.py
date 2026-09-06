"""Canonical JSON encoding for values persisted in SQLite TEXT columns."""

from __future__ import annotations

import json
from typing import Any, TypeVar


T = TypeVar("T")


def dumps_json(value: Any, *, sort_keys: bool = False) -> str:
    """Serialize a database JSON value consistently and without ASCII escaping."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
        sort_keys=sort_keys,
    )


def loads_json(value: Any, default: T) -> Any | T:
    """Decode a SQLite JSON TEXT value, returning the supplied default if invalid."""
    if value is None or value == "":
        return default
    try:
        return json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return default
