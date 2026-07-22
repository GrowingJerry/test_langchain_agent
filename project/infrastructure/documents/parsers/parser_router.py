"""Select an installed parser without making optional dependencies mandatory."""

from __future__ import annotations

from infrastructure.documents.parsers.base import DocumentParser
from infrastructure.documents.parsers.docling_parser import DoclingParser
from infrastructure.documents.parsers.pymupdf_parser import PyMuPDFParser
from infrastructure.documents.ocr.rapidocr_engine import RapidOcrEngine
from config.settings import settings


class ParserRouter:
    def select(self, parser_type: str = "pymupdf") -> DocumentParser:
        requested = str(parser_type or "pymupdf").strip().lower()
        if requested == "docling":
            parser = DoclingParser()
            if parser.is_available():
                return parser
        ocr_engine = None
        if settings.document_ocr_enabled and settings.document_ocr_engine == "rapidocr":
            candidate = RapidOcrEngine(settings.document_ocr_min_confidence)
            if candidate.is_available():
                ocr_engine = candidate
        parser = PyMuPDFParser(ocr_engine=ocr_engine, ocr_dpi=settings.document_ocr_dpi)
        if not parser.is_available():
            raise RuntimeError("PyMuPDF is required for PDF parsing")
        return parser
