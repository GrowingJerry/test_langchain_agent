"""In-process background generation tasks with cooperative stream cancellation."""
from __future__ import annotations
import threading, time, uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

@dataclass
class GenerationTask:
    task_id: str
    future: Future
    cancel_event: threading.Event
    started_at: float
    requirement_id: str
    model: str
    mode: str
    def cancel(self) -> None: self.cancel_event.set()

_executor=ThreadPoolExecutor(max_workers=2, thread_name_prefix="case-generation")

def submit_generation(fn: Callable[[Callable[[], bool]], Any], requirement_id: str, model: str, mode: str) -> GenerationTask:
    event=threading.Event(); future=_executor.submit(fn, event.is_set)
    return GenerationTask("TASK-"+uuid.uuid4().hex[:12].upper(),future,event,time.monotonic(),requirement_id,model,mode)
