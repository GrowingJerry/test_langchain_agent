"""Lazy, optional RapidOCR adapter."""

from __future__ import annotations

from statistics import fmean
from typing import Any, List

from document_ocr.base import OcrPageResult


class RapidOcrEngine:
    engine_name = "rapidocr"

    def __init__(self, minimum_confidence: float = 0.55) -> None:
        self.minimum_confidence = minimum_confidence
        self._engine: Any = None

    def is_available(self) -> bool:
        try:
            import rapidocr  # noqa: F401
            import onnxruntime  # noqa: F401
        except ImportError:
            return False
        return True

    def _get_engine(self) -> Any:
        if self._engine is None:
            from rapidocr import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    def recognize(self, image_bytes: bytes) -> OcrPageResult:
        result = self._get_engine()(image_bytes)
        texts: List[str] = []
        scores: List[float] = []
        if hasattr(result, "txts"):
            texts = [str(item).strip() for item in (result.txts or ()) if str(item).strip()]
            scores = [float(item) for item in (result.scores or ())]
        elif isinstance(result, tuple):
            # Compatibility with the former rapidocr-onnxruntime return shape.
            rows = result[0] or []
            texts = [str(row[1]).strip() for row in rows if len(row) >= 3 and str(row[1]).strip()]
            scores = [float(row[2]) for row in rows if len(row) >= 3]
        confidence = fmean(scores) if scores else None
        warnings = []
        if not texts:
            warnings.append("RapidOCR未识别到文本")
        elif confidence is not None and confidence < self.minimum_confidence:
            warnings.append(
                f"RapidOCR平均置信度{confidence:.3f}低于阈值{self.minimum_confidence:.3f}"
            )
        return OcrPageResult(
            text="\n".join(texts), confidence=confidence,
            engine=self.engine_name, line_count=len(texts), warnings=warnings,
        )
