"""Small OCR boundary that keeps optional engines out of ingestion code."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Protocol


@dataclass(slots=True)
class OcrPageResult:
    text: str
    confidence: float | None
    engine: str
    line_count: int = 0
    warnings: List[str] = field(default_factory=list)


class OcrEngine(Protocol):
    engine_name: str

    def is_available(self) -> bool: ...

    def recognize(self, image_bytes: bytes) -> OcrPageResult: ...
