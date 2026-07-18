# -*- coding: utf-8 -*-
"""Upload, parse, and chunk project documents for the project knowledge base."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

from core.document_parser import read_docx_text
from core.project_manager import ProjectManager


SUPPORTED_TYPES = {".txt", ".md", ".docx", ".pdf"}


def safe_filename(filename: str) -> str:
    """Keep an uploaded filename local and filesystem friendly."""
    name = Path(filename or "uploaded.txt").name
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip() or "uploaded.txt"


def _normalize_extracted_text(text: str) -> str:
    """Normalize parser output while keeping paragraph breaks readable."""
    text = re.sub(r"\r\n?", "\n", text or "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def read_pdf_pages(path: Path) -> Tuple[List[Dict[str, object]], str]:
    """Extract copyable text from each PDF page using PyMuPDF."""
    try:
        import fitz
    except ImportError as exc:
        return [], (
            "PDF 解析需要安装 PyMuPDF：请执行 python -m pip uninstall fitz -y && "
            f"python -m pip install PyMuPDF。当前 fitz 导入失败：{exc}；"
            "扫描 PDF 后续需使用 OCR 或视觉模型处理。"
        )

    pages: List[Dict[str, object]] = []
    try:
        with fitz.open(path) as doc:
            if doc.page_count == 0:
                return [], "PDF 页数为 0，未解析到正文；后续可使用 OCR 或视觉模型处理。"
            for index, page in enumerate(doc, start=1):
                text = _normalize_extracted_text(page.get_text("text"))
                if text:
                    pages.append(
                        {"page_no": index, "text": text, "source_type": "pdf_text"}
                    )
    except Exception as exc:
        return [], f"PDF 解析失败：{exc}；后续可使用 OCR 或视觉模型处理。"

    if not pages:
        return (
            [],
            "PDF 未解析到可复制文本，可能为空 PDF 或扫描 PDF；后续可使用 OCR 或视觉模型处理。",
        )
    return pages, ""


def read_project_document_pages(path: Path) -> Tuple[List[Dict[str, object]], str]:
    """Read supported project documents as page-aware text records."""
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        text = _normalize_extracted_text(
            path.read_text(encoding="utf-8", errors="replace")
        )
        return (
            [{"page_no": None, "text": text, "source_type": suffix.lstrip(".")}]
            if text
            else []
        ), ""
    if suffix == ".docx":
        text = _normalize_extracted_text(read_docx_text(path))
        return (
            [{"page_no": None, "text": text, "source_type": "docx"}] if text else []
        ), ""
    if suffix == ".pdf":
        return read_pdf_pages(path)
    raise ValueError(f"Unsupported project document type: {suffix}")


def read_project_document_text(path: Path) -> Tuple[str, str]:
    """Read supported project document text, keeping PDF page labels in text."""
    pages, warning = read_project_document_pages(path)
    parts = []
    for page in pages:
        text = str(page.get("text") or "")
        page_no = page.get("page_no")
        if page_no:
            parts.append(f"[第 {page_no} 页]\n{text}")
        else:
            parts.append(text)
    return "\n\n".join(parts), warning


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 120) -> List[str]:
    """Split text into overlapping chunks while preserving paragraph boundaries."""
    cleaned = re.sub(r"\r\n?", "\n", text or "")
    paragraphs = [
        p.strip() for p in re.split(r"\n\s*\n|(?<=。)\s*\n", cleaned) if p.strip()
    ]
    chunks: List[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            current = f"{current}\n\n{para}".strip()
            continue
        if current:
            chunks.append(current)
        if len(para) <= chunk_size:
            current = para
        else:
            start = 0
            while start < len(para):
                chunks.append(para[start : start + chunk_size].strip())
                start += max(chunk_size - overlap, 1)
            current = ""
    if current:
        chunks.append(current)

    compact = []
    for chunk in chunks:
        s = re.sub(r"\n{3,}", "\n\n", chunk).strip()
        if s:
            compact.append(s)
    return compact


def build_document_chunks(path: Path) -> Tuple[List[Dict[str, object]], int, str]:
    """Parse a saved document and return chunk rows with source metadata."""
    pages, warning = read_project_document_pages(path)
    chunks: List[Dict[str, object]] = []
    text_chars = 0
    for page in pages:
        page_text = str(page.get("text") or "")
        text_chars += len(page_text)
        for chunk in chunk_text(page_text):
            chunks.append(
                {
                    "chunk_text": chunk,
                    "page_no": page.get("page_no"),
                    "source_type": page.get("source_type")
                    or path.suffix.lower().lstrip("."),
                }
            )
    return chunks, text_chars, warning


def save_and_ingest_document(
    manager: ProjectManager,
    project_id: str,
    filename: str,
    content: bytes,
) -> Dict[str, object]:
    """Save an uploaded file, parse text, write chunks, and return ingestion stats."""
    clean_name = safe_filename(filename)
    suffix = Path(clean_name).suffix.lower()
    if suffix not in SUPPORTED_TYPES:
        raise ValueError("仅支持 txt、md、docx、pdf 文件")

    upload_dir = manager.uploads_dir(project_id)
    target = upload_dir / clean_name
    if target.exists():
        target = (
            upload_dir
            / f"{target.stem}_{len(list(upload_dir.glob(target.stem + '*'))) + 1}{target.suffix}"
        )
    target.write_bytes(content)

    document_id = manager.add_document(
        project_id=project_id,
        filename=target.name,
        file_type=suffix.lstrip("."),
        file_path=target,
    )
    chunks, text_chars, warning = build_document_chunks(target)
    chunk_ids = manager.replace_chunks(project_id, document_id, chunks)
    embedding_count = 0
    try:
        from core.project_kb import ensure_embeddings_for_chunks

        chunk_rows = [
            row
            for row in manager.list_chunks(project_id, limit=5000)
            if row.get("document_id") == document_id
        ]
        embedding_count = ensure_embeddings_for_chunks(manager, project_id, chunk_rows)
    except Exception as exc:
        embedding_count = 0
        warning = "；".join(
            part
            for part in (
                warning,
                f"embedding 降级：{type(exc).__name__}: {exc}",
            )
            if part
        )
    return {
        "document_id": document_id,
        "filename": target.name,
        "file_path": str(target),
        "text_chars": text_chars,
        "chunk_count": len(chunk_ids),
        "embedding_count": embedding_count,
        "warning": warning,
    }
