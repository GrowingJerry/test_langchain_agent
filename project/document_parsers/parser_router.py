"""Select an installed parser without making optional dependencies mandatory."""

from __future__ import annotations

from document_parsers.base import DocumentParser
from document_parsers.docling_parser import DoclingParser
from document_parsers.pymupdf_parser import PyMuPDFParser


class ParserRouter:
    def select(self, parser_type: str = "pymupdf") -> DocumentParser:
        requested = str(parser_type or "pymupdf").strip().lower()
        if requested == "docling":
            parser = DoclingParser()
            if parser.is_available():
                return parser
        parser = PyMuPDFParser()
        if not parser.is_available():
            raise RuntimeError("PyMuPDF is required for PDF parsing")
        return parser

