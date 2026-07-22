"""Optional page OCR engines used by document parsers."""

from document_ocr.base import OcrPageResult
from document_ocr.rapidocr_engine import RapidOcrEngine

__all__ = ["OcrPageResult", "RapidOcrEngine"]
