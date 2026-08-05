from pathlib import Path

import pytest

import application.services.project_service as manager_module
from config.settings import Settings
from application.services.project_service import ProjectManager
from application.services.upload_service import UploadService, UploadValidationError


@pytest.fixture
def upload_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[UploadService, str, Path]:
    monkeypatch.setattr(manager_module, "PROJECT_OUTPUT_ROOT", tmp_path / "projects")
    manager = ProjectManager(tmp_path / "workspace.db")
    project_id = manager.create_project("上传安全")["project_id"]
    return (
        UploadService(manager, Settings(enable_ollama=False, upload_max_bytes=32)),
        project_id,
        tmp_path,
    )


def test_rejects_empty_oversize_and_disallowed_files(
    upload_service: tuple[UploadService, str, Path],
) -> None:
    service, project_id, _ = upload_service
    with pytest.raises(UploadValidationError, match="为空"):
        service.save(project_id, "a.txt", b"")
    with pytest.raises(UploadValidationError, match="大小限制"):
        service.save(project_id, "a.txt", b"x" * 33)
    with pytest.raises(UploadValidationError, match="扩展名"):
        service.save(project_id, "macro.docm", b"x")


def test_path_traversal_is_sanitized_and_confined(
    upload_service: tuple[UploadService, str, Path],
) -> None:
    service, project_id, _ = upload_service
    target = service.save(project_id, "../../outside.txt", b"safe")
    uploads = service.manager.uploads_dir(project_id).resolve()
    assert target.resolve().parent == uploads
    assert target.name == "outside.txt"
    assert target.read_bytes() == b"safe"


def test_invalid_docx_parse_is_isolated(
    upload_service: tuple[UploadService, str, Path],
) -> None:
    service, project_id, _ = upload_service
    result = service.ingest_document(project_id, "broken.docx", b"not-a-zip")
    assert result["chunk_count"] == 0
    assert result["warning"]
    assert service.manager.list_documents(project_id)


def test_large_document_is_queued_without_synchronous_parse(
    upload_service: tuple[UploadService, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project_id, _ = upload_service
    monkeypatch.setattr(service, "_requires_background", lambda *_: True)
    result = service.ingest_document(project_id, "book.pdf", b"pdf")
    assert result["background"] is True
    assert result["job_status"] == "pending"
    jobs = service.manager.connections.connect()
    try:
        row = jobs.execute(
            "SELECT status FROM document_processing_jobs WHERE job_id=?",
            (result["job_id"],),
        ).fetchone()
    finally:
        jobs.close()
    assert row["status"] == "pending"


def test_legacy_doc_upload_is_allowed_with_text_fallback(
    upload_service: tuple[UploadService, str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project_id, _ = upload_service

    class Parsed:
        text = "REQ-FUNC-001 系统应支持查询。"

    monkeypatch.setattr(
        "infrastructure.documents.ingestor.read_word_document",
        lambda path: Parsed(),
    )
    result = service.ingest_document(project_id, "legacy.doc", b"doc-bytes")
    assert result["chunk_count"] == 1
    assert "legacy .doc" in result["parse_report"]["warnings"][0]
