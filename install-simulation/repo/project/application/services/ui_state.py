"""Pure helpers for Streamlit generation idempotency state."""

import hashlib
import json
from typing import Any, Mapping, MutableMapping


def request_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def begin_once(state: MutableMapping[str, Any], key: str, fingerprint: str) -> bool:
    if state.get(f"{key}:running") or state.get(f"{key}:completed") == fingerprint:
        return False
    state[f"{key}:running"] = fingerprint
    return True


def finish_once(
    state: MutableMapping[str, Any], key: str, fingerprint: str, result: Any = None
) -> None:
    state.pop(f"{key}:running", None)
    state[f"{key}:completed"] = fingerprint
    if result is not None:
        state[f"{key}:result"] = result


def fail_once(state: MutableMapping[str, Any], key: str) -> None:
    state.pop(f"{key}:running", None)
