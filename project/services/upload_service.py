"""Project-scoped safe upload validation, storage, and parsing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from config.settings import Settings, settings as default_settings
from core.document_ingestor import SUPPORTED_TYPES, build_document_chunks, safe_filename

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
        target = self.save(project_id, filename, content, SUPPORTED_TYPES)
        try:
            chunks, text_chars, warning = build_document_chunks(target)
        except Exception as exc:
            # Parser libraries expose different exception types; this is the single
            # isolation boundary and the diagnostic context is returned to the UI.
            chunks, text_chars, warning = (
                [],
                0,
                f"文档解析失败：{type(exc).__name__}: {exc}",
            )
        if text_chars > self.settings.document_max_chars:
            raise UploadValidationError("解析文本超过资源限制")
        document_id = self.manager.add_document(
            project_id, target.name, target.suffix.lstrip("."), target
        )
        chunk_ids = self.manager.replace_chunks(project_id, document_id, chunks)
        return {
            "document_id": document_id,
            "filename": target.name,
            "file_path": str(target),
            "text_chars": text_chars,
            "chunk_count": len(chunk_ids),
            "embedding_count": 0,
            "warning": warning or ("文件未解析出有效文本" if not chunks else ""),
        }

    def save_history(self, project_id: str, filename: str, content: bytes) -> Path:
        return self.save(project_id, filename, content, HISTORY_TYPES)

    def save_image(self, project_id: str, filename: str, content: bytes) -> Path:
        return self.save(project_id, filename, content, IMAGE_TYPES)
