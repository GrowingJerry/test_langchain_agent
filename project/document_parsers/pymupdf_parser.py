"""Default fault-isolated PDF parser using PyMuPDF."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from document_parsers.base import ParsedPage
from document_ocr.base import OcrEngine


CHAPTER_RE = re.compile(r"^(?:第[一二三四五六七八九十百零〇\d]+章\s*.+|CHAPTER\s+\d+.*)$", re.I)
SECTION_RE = re.compile(r"^(?:\d+(?:\.\d+){1,4}\s+\S.+|第[一二三四五六七八九十百零〇\d]+节\s*.+)$")
TABLE_RE = re.compile(r"^(?:表|Table)\s*[A-Za-z0-9一二三四五六七八九十.-]+\s*.+$", re.I)
FIGURE_RE = re.compile(r"^(?:图|Figure|Fig\.)\s*[A-Za-z0-9一二三四五六七八九十.-]+\s*.+$", re.I)
EQUATION_RE = re.compile(r"(?:式\s*[（(]?\d+(?:[.-]\d+)*[）)]?|[（(]\d+(?:[.-]\d+)+[）)])\s*$")


def normalize_page_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text or "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def detect_page_markers(page: ParsedPage) -> None:
    for line in page.text.splitlines():
        source = line.strip()
        if not source or len(source) > 180:
            continue
        if CHAPTER_RE.match(source):
            page.chapter_titles.append(source)
        elif SECTION_RE.match(source):
            page.section_titles.append(source)
        if TABLE_RE.match(source):
            page.table_titles.append(source)
        if FIGURE_RE.match(source):
            page.figure_titles.append(source)
        if EQUATION_RE.search(source):
            page.equation_numbers.append(source)


class PyMuPDFParser:
    parser_type = "pymupdf"

    def __init__(
        self,
        scanned_text_threshold: int = 20,
        ocr_engine: OcrEngine | None = None,
        ocr_dpi: int = 220,
    ) -> None:
        self.scanned_text_threshold = scanned_text_threshold
        self.ocr_engine = ocr_engine
        self.ocr_dpi = ocr_dpi

    def is_available(self) -> bool:
        try:
            import fitz  # noqa: F401
        except ImportError:
            return False
        return True

    def page_count(self, path: Path) -> int:
        import fitz

        with fitz.open(path) as document:
            return document.page_count

    def iter_pages(self, path: Path, start_page: int = 1) -> Iterator[ParsedPage]:
        import fitz

        with fitz.open(path) as document:
            for page_index in range(max(start_page - 1, 0), document.page_count):
                page_no = page_index + 1
                try:
                    page = document.load_page(page_index)
                    text = normalize_page_text(page.get_text("text"))
                    count = len(text)
                    needs_ocr = count < self.scanned_text_threshold
                    warning = ""
                    ocr_applied = False
                    ocr_engine = ""
                    ocr_confidence = None
                    if needs_ocr and self.ocr_engine is not None:
                        try:
                            scale = self.ocr_dpi / 72
                            pixmap = page.get_pixmap(
                                matrix=fitz.Matrix(scale, scale), alpha=False
                            )
                            ocr = self.ocr_engine.recognize(pixmap.tobytes("png"))
                            del pixmap
                            ocr_applied = True
                            ocr_engine = ocr.engine
                            ocr_confidence = ocr.confidence
                            ocr_text = normalize_page_text(ocr.text)
                            if ocr_text:
                                text = ocr_text
                                count = len(text)
                                needs_ocr = False
                            warning = "；".join(ocr.warnings)
                        except Exception as exc:
                            warning = f"RapidOCR页面识别失败：{type(exc).__name__}: {exc}"
                    elif needs_ocr:
                        warning = "页面文本过少，可能为扫描页；已标记needs_ocr，未自动执行OCR"
                    result = ParsedPage(
                        page_no=page_no,
                        text=text,
                        parser_type=self.parser_type,
                        text_char_count=count,
                        needs_ocr=needs_ocr,
                        parse_warning=warning,
                        ocr_applied=ocr_applied,
                        ocr_engine=ocr_engine,
                        ocr_confidence=ocr_confidence,
                        ocr_dpi=self.ocr_dpi if ocr_applied else None,
                    )
                    detect_page_markers(result)
                    yield result
                except Exception as exc:
                    yield ParsedPage(
                        page_no=page_no,
                        text="",
                        parser_type=self.parser_type,
                        text_char_count=0,
                        needs_ocr=False,
                        parse_warning=f"页面解析失败：{type(exc).__name__}: {exc}",
                    )
