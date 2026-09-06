"""Optional Docling adapter with safe PyMuPDF page-boundary fallback."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Dict, Iterator, List

from infrastructure.documents.parsers.base import ParsedPage
from infrastructure.documents.parsers.pymupdf_parser import PyMuPDFParser


class DoclingParser:
    parser_type = "docling"

    def __init__(self) -> None:
        self._fallback = PyMuPDFParser()

    def is_available(self) -> bool:
        return importlib.util.find_spec("docling") is not None

    def page_count(self, path: Path) -> int:
        return self._fallback.page_count(path)

    def iter_pages(self, path: Path, start_page: int = 1) -> Iterator[ParsedPage]:
        if not self.is_available():
            raise RuntimeError("Docling is not installed")
        enhancements: Dict[int, Dict[str, List[str]]] = {}
        enhancement_warning = ""
        try:
            from docling.document_converter import DocumentConverter

            converted = DocumentConverter().convert(str(path))
            document = converted.document
            iterator = getattr(document, "iterate_items", None)
            if callable(iterator):
                for entry in iterator():
                    item = entry[0] if isinstance(entry, tuple) else entry
                    text = str(
                        getattr(item, "text", "")
                        or getattr(item, "caption_text", "")
                        or ""
                    ).strip()
                    label = str(getattr(item, "label", "")).lower()
                    provenance = list(getattr(item, "prov", None) or [])
                    page_numbers = {
                        int(getattr(prov, "page_no", 0) or 0) for prov in provenance
                    }
                    for page_no in page_numbers - {0}:
                        target = enhancements.setdefault(
                            page_no,
                            {
                                "chapter_titles": [],
                                "section_titles": [],
                                "table_titles": [],
                                "figure_titles": [],
                                "equation_numbers": [],
                            },
                        )
                        if text and "section_header" in label:
                            target["section_titles"].append(text)
                        elif text and "table" in label:
                            target["table_titles"].append(text)
                        elif text and ("picture" in label or "figure" in label):
                            target["figure_titles"].append(text)
                        elif text and ("formula" in label or "equation" in label):
                            target["equation_numbers"].append(text)
        except Exception as exc:
            enhancement_warning = (
                f"Docling增强解析失败，已保留PyMuPDF页结果：{type(exc).__name__}: {exc}"
            )
        for page in self._fallback.iter_pages(path, start_page):
            page.parser_type = self.parser_type
            extra = enhancements.get(page.page_no, {})
            for field in (
                "chapter_titles",
                "section_titles",
                "table_titles",
                "figure_titles",
                "equation_numbers",
            ):
                values = getattr(page, field)
                for value in extra.get(field, []):
                    if value not in values:
                        values.append(value)
            if enhancement_warning:
                page.parse_warning = "；".join(
                    value
                    for value in (page.parse_warning, enhancement_warning)
                    if value
                )
            yield page
