"""Contracts shared by page-aware parser adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Protocol


@dataclass(slots=True)
class ParsedPage:
    page_no: int
    text: str
    parser_type: str
    text_char_count: int
    needs_ocr: bool = False
    parse_warning: str = ""
    ocr_applied: bool = False
    ocr_engine: str = ""
    ocr_confidence: float | None = None
    ocr_dpi: int | None = None
    chapter_titles: List[str] = field(default_factory=list)
    section_titles: List[str] = field(default_factory=list)
    table_titles: List[str] = field(default_factory=list)
    figure_titles: List[str] = field(default_factory=list)
    equation_numbers: List[str] = field(default_factory=list)

    def to_legacy_dict(self) -> dict[str, object]:
        return {
            "page_no": self.page_no,
            "text": self.text,
            "source_type": "pdf_ocr" if self.ocr_applied else "pdf_text",
            "parser_type": self.parser_type,
            "text_char_count": self.text_char_count,
            "needs_ocr": self.needs_ocr,
            "parse_warning": self.parse_warning,
            "ocr_applied": self.ocr_applied,
            "ocr_engine": self.ocr_engine,
            "ocr_confidence": self.ocr_confidence,
            "ocr_dpi": self.ocr_dpi,
            "chapter_titles": list(self.chapter_titles),
            "section_titles": list(self.section_titles),
            "table_titles": list(self.table_titles),
            "figure_titles": list(self.figure_titles),
            "equation_numbers": list(self.equation_numbers),
        }


class DocumentParser(Protocol):
    parser_type: str

    def is_available(self) -> bool: ...

    def page_count(self, path: Path) -> int: ...

    def iter_pages(self, path: Path, start_page: int = 1) -> Iterator[ParsedPage]: ...
