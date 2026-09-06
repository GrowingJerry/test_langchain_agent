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

    def collect(self, events: Iterable[dict[str, Any]], callback=None, activity_callback=None) -> tuple[dict[str, Any], str]:
        started=time.monotonic(); last_activity=started; content=""; last={}
        for event in events:
            now=time.monotonic(); last_activity=now
            last=event; part=str(((event.get("message") or {}).get("content")) or "")
            content += part
            if callback and part: callback(part)
            if activity_callback: activity_callback(now)
            reason=self.reason(content, now-started, now-last_activity)
            if self.cancelled(): reason="client_cancelled"
            if reason: raise GenerationTerminated(reason, content)
        return last, content

    def reason(self, text: str, elapsed: float=0, idle_elapsed: float=0) -> str:
        s=self.settings
        call_limit=getattr(s,"effective_model_call_max_seconds",s.generation_max_seconds)
        if call_limit and elapsed > call_limit: return "model_call_timeout"
        idle_limit=getattr(s,"generation_idle_timeout_seconds",0)
        if idle_limit and idle_elapsed > idle_limit: return "idle_timeout"
        if len(text) > s.generation_max_output_chars: return "max_output_chars"
        if not s.generation_repetition_guard_enabled: return ""
        if re.search(rf"(.)\1{{{s.generation_max_same_char_run-1},}}", text, re.S): return "repetition_detected"
        if re.search(rf"\d{{{s.generation_max_digit_run+1},}}", text): return "repetition_detected"
        w=s.generation_repeat_window_chars
        if len(text)>=w*s.generation_repeat_threshold and text[-w:]*s.generation_repeat_threshold in text[-w*s.generation_repeat_threshold:]: return "repetition_detected"
        return ""
