"""Project-scoped safe upload validation, storage, and parsing."""

from __future__ import annotations

from pathlib import Path
import hashlib
from typing import Any, Dict

from config.settings import Settings, settings as default_settings
from infrastructure.documents.ingestor import (
    SUPPORTED_TYPES,
    safe_filename,
    save_and_ingest_document,
)
from workflows.learning.job_service import DocumentJobService

HISTORY_TYPES = {".xlsx", ".csv", ".json"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}


class UploadValidationError(ValueError):
    pass


class UploadService:
    def __init__(self, manager: Any, settings: Settings = default_settings) -> None:
        self.manager = manager
        self.settings = settings

    def validate(
        self, filename: str, content: bytes, allowed: set[str] = SUPPORTED_TYPES
    ) -> str:
        clean = safe_filename(filename)
        if clean in {".", ".."} or not clean:
            raise UploadValidationError("文件名无效")
        if Path(clean).suffix.lower() not in allowed:
            raise UploadValidationError("文件扩展名不在允许列表中")
        if not content:
            raise UploadValidationError("上传文件为空")
        if len(content) > self.settings.upload_max_bytes:
            raise UploadValidationError(
                f"文件超过大小限制 {self.settings.upload_max_bytes} bytes"
            )
        return clean

    @staticmethod
    def _safe_target(root: Path, filename: str) -> Path:
        resolved_root = root.resolve()
        target = (resolved_root / filename).resolve()
        if target.parent != resolved_root:
            raise UploadValidationError("上传路径超出当前项目目录")
        return target

    def save(
        self,
        project_id: str,
        filename: str,
        content: bytes,
        allowed: set[str] = SUPPORTED_TYPES,
    ) -> Path:
        clean = self.validate(filename, content, allowed)
        root = self.manager.uploads_dir(project_id)
        target = self._safe_target(root, clean)
        if target.exists():
            target = self._safe_target(
                root,
                f"{target.stem}_{len(list(root.glob(target.stem + '*'))) + 1}{target.suffix}",
            )
        target.write_bytes(content)
        return target

    def ingest_document(
        self, project_id: str, filename: str, content: bytes
    ) -> Dict[str, object]:
        clean = self.validate(filename, content, SUPPORTED_TYPES)
        if self._requires_background(clean, content):
            return self._enqueue_document(project_id, clean, content)
        try:
            result = save_and_ingest_document(
                self.manager, project_id, filename, content
            )
        except Exception as exc:
            raise UploadValidationError(
                f"文档解析或入库失败：{type(exc).__name__}: {exc}"
            ) from exc
        if (
            int(result.get("text_chars") or 0) > self.settings.document_max_chars
            and Path(filename).suffix.lower() != ".pdf"
        ):
            raise UploadValidationError("解析文本超过资源限制")
        return result

    def _requires_background(self, filename: str, content: bytes) -> bool:
        if len(content) >= self.settings.background_document_bytes_threshold:
            return True
        if Path(filename).suffix.lower() != ".pdf":
            return False
        try:
            import fitz

            with fitz.open(stream=content, filetype="pdf") as document:
                return document.page_count >= self.settings.background_document_page_threshold
        except Exception:
            return False

    def _enqueue_document(
        self, project_id: str, filename: str, content: bytes
    ) -> Dict[str, object]:
        content_hash = hashlib.sha256(content).hexdigest()
        existing = self.manager.get_document_by_hash(project_id, content_hash)
        if existing:
            document_id = str(existing["document_id"])
            target = Path(str(existing["file_path"]))
        else:
            target = self.save(project_id, filename, content, SUPPORTED_TYPES)
            document_id = self.manager.add_document(
                project_id,
                target.name,
                target.suffix.lstrip("."),
                target,
                content_hash,
                "pymupdf" if target.suffix.lower() == ".pdf" else target.suffix.lstrip("."),
            )
        total_pages = 0
        if target.suffix.lower() == ".pdf":
            try:
                import fitz

                with fitz.open(target) as document:
                    total_pages = document.page_count
            except Exception:
                total_pages = 0
        job = DocumentJobService(self.manager).create_job(
            project_id, document_id, total_pages
        )
        return {
            "document_id": document_id,
            "filename": target.name,
            "file_path": str(target),
            "file_hash": content_hash,
            "chunk_count": 0,
            "embedding_count": 0,
            "background": True,
            "job_id": job["job_id"],
            "job_status": job["status"],
            "warning": "文档已进入后台处理队列",
        }

    def save_history(self, project_id: str, filename: str, content: bytes) -> Path:
        return self.save(project_id, filename, content, HISTORY_TYPES)

    def save_image(self, project_id: str, filename: str, content: bytes) -> Path:
        return self.save(project_id, filename, content, IMAGE_TYPES)
