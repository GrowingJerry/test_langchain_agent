# -*- coding: utf-8 -*-
"""Upload, stream-parse, and hierarchically chunk project documents."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from infrastructure.documents.document_parser import read_docx_text
from application.services.project_service import ProjectManager
from infrastructure.documents.parsers.base import DocumentParser, ParsedPage
from infrastructure.documents.parsers.parser_router import ParserRouter
from infrastructure.database.json_codec import loads_json


SUPPORTED_TYPES = {".txt", ".md", ".docx", ".pdf"}
BOOK_PAGE_THRESHOLD = 50
LOW_TEXT_THRESHOLD = 80
DEFAULT_CHECKPOINT_PAGES = 20
ProgressCallback = Callable[[Dict[str, Any]], None]
ChunkCheckpointCallback = Callable[[List[Dict[str, object]], Dict[str, Any]], None]


class DocumentIngestionInterrupted(RuntimeError):
    """Raised by a checkpoint controller after durable progress was saved."""


class DocumentParseReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_hash: str
    parser_type: str
    total_pages: int = 0
    successful_pages: int = 0
    low_text_pages: int = 0
    possible_scanned_pages: int = 0
    ocr_processed_pages: int = 0
    ocr_low_confidence_pages: int = 0
    section_count: int = 0
    table_marker_count: int = 0
    figure_marker_count: int = 0
    formula_marker_count: int = 0
    last_completed_page: int = 0
    warnings: List[str] = Field(default_factory=list)


def safe_filename(filename: str) -> str:
    """Keep an uploaded filename local and filesystem friendly."""
    name = Path(filename or "uploaded.txt").name
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip() or "uploaded.txt"


def file_sha256(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while block := source.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def _normalize_extracted_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text or "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def iter_project_document_pages(
    path: Path,
    *,
    parser_type: str = "pymupdf",
    start_page: int = 1,
    parser: Optional[DocumentParser] = None,
) -> Iterator[Dict[str, object]]:
    """Yield one page at a time; callers never need to retain page objects."""
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        text = _normalize_extracted_text(
            path.read_text(encoding="utf-8", errors="replace")
        )
        if text and start_page <= 1:
            yield {
                "page_no": None,
                "text": text,
                "source_type": suffix.lstrip("."),
                "parser_type": suffix.lstrip("."),
                "text_char_count": len(text),
                "needs_ocr": False,
                "parse_warning": "",
            }
        return
    if suffix == ".docx":
        text = _normalize_extracted_text(read_docx_text(path))
        if text and start_page <= 1:
            yield {
                "page_no": None,
                "text": text,
                "source_type": "docx",
                "parser_type": "docx",
                "text_char_count": len(text),
                "needs_ocr": False,
                "parse_warning": "",
            }
        return
    if suffix != ".pdf":
        raise ValueError(f"Unsupported project document type: {suffix}")
    selected = parser or ParserRouter().select(parser_type)
    for page in selected.iter_pages(path, start_page=start_page):
        yield page.to_legacy_dict()


def read_pdf_pages(path: Path) -> Tuple[List[Dict[str, object]], str]:
    """Compatibility API collecting the new streaming PyMuPDF iterator."""
    try:
        pages = list(iter_project_document_pages(path))
    except DocumentIngestionInterrupted:
        raise
    except Exception as exc:
        return [], f"PDF解析失败：{type(exc).__name__}: {exc}"
    warnings = [str(page.get("parse_warning")) for page in pages if page.get("parse_warning")]
    return pages, "；".join(warnings)


def read_project_document_pages(path: Path) -> Tuple[List[Dict[str, object]], str]:
    """Compatibility API collecting page records for existing callers."""
    try:
        pages = list(iter_project_document_pages(path))
    except Exception as exc:
        return [], f"文档解析失败：{type(exc).__name__}: {exc}"
    warnings = [str(page.get("parse_warning")) for page in pages if page.get("parse_warning")]
    return pages, "；".join(warnings)


def read_project_document_text(path: Path) -> Tuple[str, str]:
    parts: List[str] = []
    warnings: List[str] = []
    try:
        for page in iter_project_document_pages(path):
            text = str(page.get("text") or "")
            page_no = page.get("page_no")
            parts.append(f"[第 {page_no} 页]\n{text}" if page_no else text)
            if page.get("parse_warning"):
                warnings.append(str(page["parse_warning"]))
    except Exception as exc:
        warnings.append(f"文档解析失败：{type(exc).__name__}: {exc}")
    return "\n\n".join(parts), "；".join(warnings)


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 120) -> List[str]:
    """Legacy-compatible paragraph-aware chunking for ordinary documents."""
    cleaned = re.sub(r"\r\n?", "\n", text or "")
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n|(?<=。)\s*\n", cleaned)
        if paragraph.strip()
    ]
    chunks: List[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 2 <= chunk_size:
            current = f"{current}\n\n{paragraph}".strip()
            continue
        if current:
            chunks.append(current)
        if len(paragraph) <= chunk_size:
            current = paragraph
        else:
            start = 0
            while start < len(paragraph):
                chunks.append(paragraph[start : start + chunk_size].strip())
                start += max(chunk_size - overlap, 1)
            current = ""
    if current:
        chunks.append(current)
    return [
        normalized
        for chunk in chunks
        if (normalized := re.sub(r"\n{3,}", "\n\n", chunk).strip())
    ]


def _section_id(file_hash: str, title: str, page_no: int) -> str:
    value = f"{file_hash}\x1f{title}\x1f{page_no}"
    return "SEC-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _page_from_dict(row: Dict[str, object]) -> ParsedPage:
    return ParsedPage(
        page_no=int(row.get("page_no") or 1),
        text=str(row.get("text") or ""),
        parser_type=str(row.get("parser_type") or row.get("source_type") or "text"),
        text_char_count=int(row.get("text_char_count") or len(str(row.get("text") or ""))),
        needs_ocr=bool(row.get("needs_ocr")),
        parse_warning=str(row.get("parse_warning") or ""),
        ocr_applied=bool(row.get("ocr_applied")),
        ocr_engine=str(row.get("ocr_engine") or ""),
        ocr_confidence=(
            float(row["ocr_confidence"])
            if row.get("ocr_confidence") is not None
            else None
        ),
        ocr_dpi=int(row["ocr_dpi"]) if row.get("ocr_dpi") is not None else None,
        chapter_titles=list(row.get("chapter_titles") or []),
        section_titles=list(row.get("section_titles") or []),
        table_titles=list(row.get("table_titles") or []),
        figure_titles=list(row.get("figure_titles") or []),
        equation_numbers=list(row.get("equation_numbers") or []),
    )


def _merge_parse_reports(
    previous: Dict[str, Any], current: DocumentParseReport
) -> DocumentParseReport:
    """Combine a resumed segment with the last persisted checkpoint totals."""
    if not previous or int(previous.get("last_completed_page") or 0) <= 0:
        return current
    data = current.model_dump(mode="json")
    for field in (
        "successful_pages",
        "low_text_pages",
        "possible_scanned_pages",
        "ocr_processed_pages",
        "ocr_low_confidence_pages",
        "section_count",
        "table_marker_count",
        "figure_marker_count",
        "formula_marker_count",
    ):
        data[field] = int(previous.get(field) or 0) + int(data[field])
    data["warnings"] = list(
        dict.fromkeys([*(previous.get("warnings") or []), *data["warnings"]])
    )[:100]
    return DocumentParseReport.model_validate(data)


def build_document_chunks_with_report(
    path: Path,
    *,
    document_mode: str = "auto",
    parser_type: str = "pymupdf",
    start_page: int = 1,
    checkpoint_pages: int = DEFAULT_CHECKPOINT_PAGES,
    progress_callback: Optional[ProgressCallback] = None,
    chunk_checkpoint_callback: Optional[ChunkCheckpointCallback] = None,
    parser: Optional[DocumentParser] = None,
) -> Tuple[List[Dict[str, object]], int, str, DocumentParseReport]:
    """Stream pages into chunks and a resumable parse report."""
    digest = file_sha256(path)
    selected_parser = parser
    total_pages = 1
    selected_type = path.suffix.lower().lstrip(".")
    if path.suffix.lower() == ".pdf":
        selected_parser = parser or ParserRouter().select(parser_type)
        total_pages = selected_parser.page_count(path)
        selected_type = selected_parser.parser_type
    book_mode = document_mode == "book" or (
        document_mode == "auto" and path.suffix.lower() == ".pdf" and total_pages >= BOOK_PAGE_THRESHOLD
    )
    report = DocumentParseReport(
        file_hash=digest, parser_type=selected_type, total_pages=total_pages
    )
    chunks: List[Dict[str, object]] = []
    warnings: List[str] = []
    text_chars = 0
    current_parent_id = ""
    current_parent_title = ""
    current_parent_start = 0
    current_parent_end = 0
    current_parent_excerpt = ""
    checkpoint_chunk_index = 0

    def save_checkpoint() -> None:
        nonlocal checkpoint_chunk_index
        progress = report.model_dump(mode="json")
        if chunk_checkpoint_callback and len(chunks) > checkpoint_chunk_index:
            chunk_checkpoint_callback(chunks[checkpoint_chunk_index:], progress)
            checkpoint_chunk_index = len(chunks)
        if progress_callback:
            progress_callback(progress)

    def flush_parent(reset: bool = True) -> None:
        nonlocal current_parent_id, current_parent_title, current_parent_excerpt
        nonlocal current_parent_start
        if not current_parent_id or not current_parent_excerpt:
            if reset:
                current_parent_id = ""
                current_parent_title = ""
            return
        chunks.append(
            {
                "chunk_text": current_parent_excerpt,
                "page_no": current_parent_start,
                "page_start": current_parent_start,
                "page_end": current_parent_end,
                "source_type": "pdf_section",
                "parser_type": selected_type,
                "chunk_level": "parent",
                "parent_section_id": current_parent_id,
                "section_title": current_parent_title,
                "needs_ocr": False,
                "parse_warning": "",
            }
        )
        current_parent_excerpt = ""
        if reset:
            current_parent_id = ""
            current_parent_title = ""
        else:
            current_parent_start = current_parent_end + 1

    iterator = iter_project_document_pages(
        path,
        parser_type=parser_type,
        start_page=start_page,
        parser=selected_parser,
    )
    for row in iterator:
        page = _page_from_dict(row)
        report.last_completed_page = page.page_no
        text_chars += page.text_char_count
        if page.parse_warning:
            warning = f"第{page.page_no}页：{page.parse_warning}"
            warnings.append(warning)
            if len(report.warnings) < 100:
                report.warnings.append(warning)
        if page.text_char_count < LOW_TEXT_THRESHOLD:
            report.low_text_pages += 1
        if page.needs_ocr or page.ocr_applied:
            report.possible_scanned_pages += 1
        if page.ocr_applied:
            report.ocr_processed_pages += 1
            if page.ocr_confidence is None or page.ocr_confidence < 0.55:
                report.ocr_low_confidence_pages += 1
        if not page.parse_warning.startswith("页面解析失败"):
            report.successful_pages += 1
        report.table_marker_count += len(page.table_titles)
        report.figure_marker_count += len(page.figure_titles)
        report.formula_marker_count += len(page.equation_numbers)
        if book_mode:
            heading = next(iter([*page.chapter_titles, *page.section_titles]), "")
            if heading:
                flush_parent()
                current_parent_id = _section_id(digest, heading, page.page_no)
                current_parent_title = heading
                current_parent_start = page.page_no
                report.section_count += 1
            elif not current_parent_id:
                current_parent_title = "前置内容"
                current_parent_id = _section_id(
                    digest, current_parent_title, page.page_no
                )
                current_parent_start = page.page_no
                report.section_count += 1
            current_parent_end = page.page_no
            if len(current_parent_excerpt) < 8000 and page.text:
                remaining = 8000 - len(current_parent_excerpt)
                current_parent_excerpt = (
                    f"{current_parent_excerpt}\n\n{page.text[:remaining]}"
                ).strip()
        for child_index, child in enumerate(chunk_text(page.text)):
            chunks.append(
                {
                    "chunk_text": child,
                    "page_no": page.page_no if path.suffix.lower() == ".pdf" else None,
                    "page_start": page.page_no if path.suffix.lower() == ".pdf" else None,
                    "page_end": page.page_no if path.suffix.lower() == ".pdf" else None,
                    "source_type": str(row.get("source_type") or selected_type),
                    "parser_type": page.parser_type,
                    "chunk_level": "child" if book_mode else "legacy",
                    "parent_section_id": current_parent_id if book_mode else "",
                    "child_index": child_index,
                    "needs_ocr": page.needs_ocr,
                    "parse_warning": page.parse_warning,
                    "ocr_applied": page.ocr_applied,
                    "ocr_engine": page.ocr_engine,
                    "ocr_confidence": page.ocr_confidence,
                    "ocr_dpi": page.ocr_dpi,
                    "table_titles": list(page.table_titles),
                    "figure_titles": list(page.figure_titles),
                    "equation_numbers": list(page.equation_numbers),
                }
            )
        if (
            (progress_callback or chunk_checkpoint_callback)
            and checkpoint_pages > 0
            and page.page_no % checkpoint_pages == 0
        ):
            if book_mode:
                flush_parent(reset=False)
            save_checkpoint()
    if book_mode:
        flush_parent()
    if report.last_completed_page and (
        progress_callback or chunk_checkpoint_callback
    ):
        save_checkpoint()
    return chunks, text_chars, "；".join(warnings), report


def build_document_chunks(path: Path) -> Tuple[List[Dict[str, object]], int, str]:
    """Compatibility wrapper returning the established three-value tuple."""
    chunks, text_chars, warning, _ = build_document_chunks_with_report(path)
    return chunks, text_chars, warning


def save_and_ingest_document(
    manager: ProjectManager,
    project_id: str,
    filename: str,
    content: bytes,
    *,
    progress_callback: Optional[ProgressCallback] = None,
) -> Dict[str, object]:
    """Save, parse, persist, and embed a document through the compatibility API."""
    clean_name = safe_filename(filename)
    suffix = Path(clean_name).suffix.lower()
    if suffix not in SUPPORTED_TYPES:
        raise ValueError("仅支持 txt、md、docx、pdf 文件")
    content_hash = hashlib.sha256(content).hexdigest()
    existing = manager.get_document_by_hash(project_id, content_hash)
    if existing and existing.get("processing_status") == "completed":
        existing_chunks = [
            row
            for row in manager.list_chunks(project_id, limit=5000)
            if row.get("document_id") == existing["document_id"]
        ]
        return {
            "document_id": existing["document_id"],
            "filename": existing["filename"],
            "file_path": existing["file_path"],
            "file_hash": content_hash,
            "text_chars": sum(len(str(row.get("content") or "")) for row in existing_chunks),
            "chunk_count": len(existing_chunks),
            "embedding_count": 0,
            "parse_report": loads_json(existing.get("parse_report_json"), {}),
            "warning": "相同文件内容已完成解析，本次跳过重复解析",
            "duplicate": True,
        }
    if existing:
        document_id = str(existing["document_id"])
        target = Path(str(existing["file_path"]))
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    else:
        upload_dir = manager.uploads_dir(project_id)
        target = upload_dir / clean_name
        if target.exists():
            target = upload_dir / (
                f"{target.stem}_{len(list(upload_dir.glob(target.stem + '*'))) + 1}{target.suffix}"
            )
        target.write_bytes(content)
        document_id = manager.add_document(
            project_id=project_id,
            filename=target.name,
            file_type=suffix.lstrip("."),
            file_path=target,
            file_hash=content_hash,
            parser_type="pymupdf" if suffix == ".pdf" else suffix.lstrip("."),
        )
    start_page = (
        int(existing.get("last_processed_page") or 0) + 1 if existing else 1
    )
    resume_base_report = (
        loads_json(existing.get("parse_report_json"), {}) if existing else {}
    )
    def persist_checkpoint(
        checkpoint_chunks: List[Dict[str, object]], progress: Dict[str, Any]
    ) -> None:
        merged = _merge_parse_reports(
            resume_base_report, DocumentParseReport.model_validate(progress)
        )
        manager.append_chunks(
            project_id,
            document_id,
            checkpoint_chunks,
            merged.model_dump(mode="json"),
            "processing",
        )
        if progress_callback:
            progress_callback(merged.model_dump(mode="json"))

    parse_failed = False
    try:
        _, text_chars, warning, report = build_document_chunks_with_report(
            target,
            start_page=start_page,
            chunk_checkpoint_callback=persist_checkpoint,
        )
    except DocumentIngestionInterrupted:
        raise
    except Exception as exc:
        parse_failed = True
        warning = f"文档解析失败：{type(exc).__name__}: {exc}"
        text_chars = 0
        latest = manager.get_document_by_hash(project_id, content_hash) or {}
        latest_report = loads_json(latest.get("parse_report_json"), {})
        if latest_report.get("last_completed_page"):
            report = DocumentParseReport.model_validate(latest_report)
            report.warnings = list(dict.fromkeys([*report.warnings, warning]))[:100]
        else:
            report = DocumentParseReport(
                file_hash=content_hash,
                parser_type="pymupdf" if suffix == ".pdf" else suffix.lstrip("."),
                warnings=[warning],
            )
    report = _merge_parse_reports(
        resume_base_report if not parse_failed else {}, report
    )
    manager.save_document_parse_progress(
        project_id,
        document_id,
        report.model_dump(mode="json"),
        "failed" if parse_failed else "completed",
    )
    embedding_count = 0
    try:
        from infrastructure.retrieval.project_knowledge import ensure_embeddings_for_chunks

        chunk_rows = [
            row
            for row in manager.list_chunks(project_id, limit=5000)
            if row.get("document_id") == document_id
        ]
        embedding_count = ensure_embeddings_for_chunks(manager, project_id, chunk_rows)
    except Exception as exc:
        warning = "；".join(
            part
            for part in (
                warning,
                f"embedding降级：{type(exc).__name__}: {exc}",
            )
            if part
        )
    return {
        "document_id": document_id,
        "filename": target.name,
        "file_path": str(target),
        "file_hash": report.file_hash,
        "text_chars": text_chars,
        "chunk_count": len(
            [
                row
                for row in manager.list_chunks(project_id, limit=5000)
                if row.get("document_id") == document_id
            ]
        ),
        "embedding_count": embedding_count,
        "parse_report": report.model_dump(mode="json"),
        "warning": warning,
    }
