from __future__ import annotations

import gc
from pathlib import Path
from typing import Iterator

import pytest

import core.project_manager as manager_module
from core.document_ingestor import (
    build_document_chunks_with_report,
    save_and_ingest_document,
)
from core.project_manager import ProjectManager
from document_parsers.base import ParsedPage


class TrackingPage:
    live = 0
    max_live = 0

    def __init__(self, page_no: int) -> None:
        type(self).live += 1
        type(self).max_live = max(type(self).max_live, type(self).live)
        self.page_no = page_no
        self.text = (
            f"第{page_no // 20 + 1}章 技术章节\n" if page_no % 20 == 1 else ""
        ) + (f"第{page_no}页技术内容。" * 20)

    def __del__(self) -> None:
        type(self).live -= 1

    def to_legacy_dict(self) -> dict[str, object]:
        heading = [self.text.splitlines()[0]] if self.page_no % 20 == 1 else []
        return {
            "page_no": self.page_no,
            "text": self.text,
            "source_type": "pdf_text",
            "parser_type": "fake_stream",
            "text_char_count": len(self.text),
            "needs_ocr": False,
            "parse_warning": "",
            "chapter_titles": heading,
            "section_titles": [],
            "table_titles": [f"表 {self.page_no}-1 参数"] if self.page_no % 25 == 0 else [],
            "figure_titles": [],
            "equation_numbers": [f"式（{self.page_no}.1）"] if self.page_no % 40 == 0 else [],
        }


class FakeTwoHundredPageParser:
    parser_type = "fake_stream"

    def __init__(self) -> None:
        self.start_pages: list[int] = []

    def is_available(self) -> bool:
        return True

    def page_count(self, path: Path) -> int:
        return 200

    def iter_pages(self, path: Path, start_page: int = 1) -> Iterator[TrackingPage]:
        self.start_pages.append(start_page)
        for page_no in range(start_page, 201):
            yield TrackingPage(page_no)


class FaultIsolatedParser:
    parser_type = "fault_isolated"

    def is_available(self) -> bool:
        return True

    def page_count(self, path: Path) -> int:
        return 3

    def iter_pages(self, path: Path, start_page: int = 1) -> Iterator[ParsedPage]:
        pages = [
            ParsedPage(1, "", self.parser_type, 0, True, "扫描页，等待OCR"),
            ParsedPage(2, "", self.parser_type, 0, False, "页面解析失败：bad page"),
            ParsedPage(3, "后续页面仍可解析。", self.parser_type, 9),
        ]
        yield from (page for page in pages if page.page_no >= start_page)


def test_streams_200_pages_without_retaining_page_objects_linearly(
    tmp_path: Path,
) -> None:
    path = tmp_path / "manual.pdf"
    path.write_bytes(b"simulated-pdf")
    parser = FakeTwoHundredPageParser()
    TrackingPage.live = 0
    TrackingPage.max_live = 0
    checkpoints: list[dict] = []
    chunks, text_chars, warning, report = build_document_chunks_with_report(
        path,
        parser=parser,
        document_mode="book",
        checkpoint_pages=20,
        progress_callback=lambda value: checkpoints.append(value),
    )
    gc.collect()
    assert TrackingPage.live == 0
    assert TrackingPage.max_live <= 2
    assert report.total_pages == 200
    assert report.successful_pages == 200
    assert report.section_count == 10
    assert report.table_marker_count == 8
    assert report.formula_marker_count == 5
    assert report.last_completed_page == 200
    assert len(checkpoints) >= 10
    assert text_chars > 0
    assert warning == ""
    children = [chunk for chunk in chunks if chunk["chunk_level"] == "child"]
    parents = [chunk for chunk in chunks if chunk["chunk_level"] == "parent"]
    assert children and parents
    assert all(child["parent_section_id"] for child in children)
    assert all(child["page_start"] == child["page_end"] for child in children)
    assert all(parent["page_start"] <= parent["page_end"] for parent in parents)


def test_resume_starts_from_requested_page(tmp_path: Path) -> None:
    path = tmp_path / "resume.pdf"
    path.write_bytes(b"resume-pdf")
    parser = FakeTwoHundredPageParser()
    _, _, _, report = build_document_chunks_with_report(
        path, parser=parser, document_mode="book", start_page=181
    )
    assert parser.start_pages == [181]
    assert report.successful_pages == 20
    assert report.last_completed_page == 200


def test_bad_page_does_not_stop_book_and_scanned_page_only_marks_ocr(
    tmp_path: Path,
) -> None:
    path = tmp_path / "faults.pdf"
    path.write_bytes(b"faults")
    chunks, _, warning, report = build_document_chunks_with_report(
        path, parser=FaultIsolatedParser(), document_mode="book"
    )
    assert report.total_pages == 3
    assert report.successful_pages == 2
    assert report.low_text_pages == 3
    assert report.possible_scanned_pages == 1
    assert report.last_completed_page == 3
    assert "第2页" in warning
    assert any(chunk.get("page_no") == 3 for chunk in chunks)


def test_file_hash_skips_completed_duplicate_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    manager = ProjectManager(tmp_path / "workspace.db")
    project_id = manager.create_project("重复解析")["project_id"]
    content = "普通需求文档内容。".encode("utf-8")
    first = save_and_ingest_document(manager, project_id, "requirements.txt", content)
    second = save_and_ingest_document(manager, project_id, "copy.txt", content)
    assert first["document_id"] == second["document_id"]
    assert second["duplicate"] is True
    assert len(manager.list_documents(project_id)) == 1
    document = manager.list_documents(project_id)[0]
    assert document["file_hash"] == first["file_hash"]
    assert document["processing_status"] == "completed"
    assert document["last_processed_page"] == 1


def test_checkpointed_chunks_resume_after_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    manager = ProjectManager(tmp_path / "resume-workspace.db")
    project_id = manager.create_project("断点恢复")["project_id"]
    path = tmp_path / "checkpoint.pdf"
    path.write_bytes(b"checkpoint-pdf")
    document_id = manager.add_document(
        project_id,
        path.name,
        "pdf",
        path,
        file_hash="checkpoint-hash",
        parser_type="fake_stream",
    )

    def interrupted(chunks: list[dict], progress: dict) -> None:
        manager.append_chunks(project_id, document_id, chunks)
        manager.save_document_parse_progress(
            project_id, document_id, progress, "processing"
        )
        if progress["last_completed_page"] == 40:
            raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        build_document_chunks_with_report(
            path,
            parser=FakeTwoHundredPageParser(),
            document_mode="book",
            checkpoint_pages=20,
            chunk_checkpoint_callback=interrupted,
        )
    document = manager.get_document_by_hash(project_id, "checkpoint-hash")
    assert document is not None
    assert document["last_processed_page"] == 40

    def resumed(chunks: list[dict], progress: dict) -> None:
        manager.append_chunks(project_id, document_id, chunks)
        manager.save_document_parse_progress(
            project_id, document_id, progress, "processing"
        )

    build_document_chunks_with_report(
        path,
        parser=FakeTwoHundredPageParser(),
        document_mode="book",
        start_page=41,
        checkpoint_pages=20,
        chunk_checkpoint_callback=resumed,
    )
    child_pages = [
        row["page_no"]
        for row in manager.list_chunks(project_id, limit=5000)
        if row.get("chunk_level") == "child"
    ]
    assert sorted(set(child_pages)) == list(range(1, 201))
