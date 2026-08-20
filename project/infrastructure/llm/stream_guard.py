"""Bounded model stream collector with deterministic repetition protection."""
from __future__ import annotations
import re, time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

class GenerationTerminated(RuntimeError):
    def __init__(self, reason: str, partial_output: str):
        super().__init__(reason); self.reason=reason; self.partial_output=partial_output

@dataclass
class StreamGuard:
    settings: Any
    cancelled: Callable[[], bool] = lambda: False

    def collect(self, events: Iterable[dict[str, Any]], callback=None) -> tuple[dict[str, Any], str]:
        started=time.monotonic(); content=""; last={}
        for event in events:
            last=event; part=str(((event.get("message") or {}).get("content")) or "")
            content += part
            if callback and part: callback(part)
            reason=self.reason(content, time.monotonic()-started)
            if self.cancelled(): reason="client_cancelled"
            if reason: raise GenerationTerminated(reason, content)
        return last, content

    def reason(self, text: str, elapsed: float=0) -> str:
        s=self.settings
        if elapsed > s.generation_max_seconds: return "timeout"
        if len(text) > s.generation_max_output_chars: return "max_output_chars"
        if not s.generation_repetition_guard_enabled: return ""
        if re.search(rf"(.)\1{{{s.generation_max_same_char_run-1},}}", text, re.S): return "repetition_detected"
        if re.search(rf"\d{{{s.generation_max_digit_run+1},}}", text): return "repetition_detected"
        w=s.generation_repeat_window_chars
        if len(text)>=w*s.generation_repeat_threshold and text[-w:]*s.generation_repeat_threshold in text[-w*s.generation_repeat_threshold:]: return "repetition_detected"
        return ""
