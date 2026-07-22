"""Optional page OCR engines used by document parsers."""

from infrastructure.documents.ocr.base import OcrPageResult
from infrastructure.documents.ocr.rapidocr_engine import RapidOcrEngine

__all__ = ["OcrPageResult", "RapidOcrEngine"]
