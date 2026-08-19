from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz", reason="optional PyMuPDF dependency is not installed")

from infrastructure.documents.ocr.base import OcrPageResult
from infrastructure.documents.parsers.pymupdf_parser import PyMuPDFParser
from application.services.project_service import ProjectManager


class FakeOcrEngine:
    engine_name = "rapidocr"

    def is_available(self) -> bool:
        return True

    def recognize(self, image_bytes: bytes) -> OcrPageResult:
        assert image_bytes.startswith(b"\x89PNG")
        return OcrPageResult(
            text="第一章 潜艇作战模型\n1.1 系统组成\n航行状态可被观测。",
            confidence=0.96,
            engine=self.engine_name,
            line_count=3,
        )


class FailingOcrEngine(FakeOcrEngine):
    def recognize(self, image_bytes: bytes) -> OcrPageResult:
        raise RuntimeError("simulated OCR failure")


def _empty_pdf(path: Path) -> None:
    document = fitz.open()
    document.new_page()
    document.save(path)
    document.close()


def test_scanned_page_is_ocrd_and_keeps_page_provenance(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    _empty_pdf(path)

    page = next(PyMuPDFParser(ocr_engine=FakeOcrEngine(), ocr_dpi=220).iter_pages(path))

    assert page.page_no == 1
    assert page.needs_ocr is False
    assert page.ocr_applied is True
    assert page.ocr_engine == "rapidocr"
    assert page.ocr_confidence == 0.96
    assert page.ocr_dpi == 220
    assert page.to_legacy_dict()["source_type"] == "pdf_ocr"
    assert "潜艇作战模型" in page.text
    assert page.chapter_titles == ["第一章 潜艇作战模型"]


def test_single_ocr_failure_does_not_abort_pdf(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    _empty_pdf(path)

    page = next(PyMuPDFParser(ocr_engine=FailingOcrEngine()).iter_pages(path))

    assert page.needs_ocr is True
    assert page.ocr_applied is False
    assert "RapidOCR页面识别失败" in page.parse_warning


def test_ocr_provenance_survives_chunk_repository_round_trip(tmp_path: Path) -> None:
    manager = ProjectManager(tmp_path / "ocr.db")
    project_id = manager.create_project("OCR来源")["project_id"]
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf")
    document_id = manager.add_document(
        project_id, source.name, "pdf", source, "ocr-hash", "pymupdf"
    )
    manager.append_chunks(project_id, document_id, [{
        "chunk_text": "识别文本", "page_no": 8, "source_type": "pdf_ocr",
        "ocr_applied": True, "ocr_engine": "rapidocr",
        "ocr_confidence": 0.93, "ocr_dpi": 220,
    }])

    stored = manager.list_chunks(project_id)[0]

    assert stored["source_type"] == "pdf_ocr"
    assert stored["ocr_engine"] == "rapidocr"
    assert stored["ocr_confidence"] == 0.93
    assert stored["ocr_dpi"] == 220
