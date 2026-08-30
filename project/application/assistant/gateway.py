"""Adapter exposing existing application services to the desktop assistant."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from application.services.project_service import DEFAULT_PROJECT_DB
from infrastructure.assistant_exporters import AssistantExporter
from infrastructure.documents.assistant_ingestor import AssistantIngestor

from .intent_router import PAGES, page_url
from .tool_registry import EmptyArgs, ToolRegistry
from .tool_result import ToolResult


class PageArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: str
    base_url: str = "http://127.0.0.1:8501"


class ProjectArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = ""


class RequirementArgs(ProjectArgs):
    requirement_id: str


class CaseArgs(ProjectArgs):
    case_id: str
    feedback: str = ""


class ParseArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    session_id: str


class SessionArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str


class CompareArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left_path: str
    right_path: str
    session_id: str


class OpenPathArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str


class TextExportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    filename: str
    content: str = Field(min_length=1)
    sources: list[str] = []


class DocxExportArgs(TextExportArgs):
    content: str | dict


class XlsxExportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str
    filename: str
    sheets: list[dict] = Field(min_length=1)
    sources: list[str] = []


class AssistantGateway:
    """One composition boundary shared by Qt and other user interfaces."""

    def __init__(self, ui_service, settings, project_root: Path) -> None:
        self.service = ui_service
        self.settings = settings
        self.project_root = project_root.resolve()
        self.current_project_id = ""
        self.ingestor = AssistantIngestor(
            self.project_root / "work" / "assistant",
            max_bytes=settings.xiaoche_max_attachment_bytes,
            max_chars=min(settings.document_max_chars, settings.xiaoche_session_context_budget * 8),
            pdf_max_pages=settings.pdf_max_pages,
        )
        self.exporter = AssistantExporter(self.project_root / settings.xiaoche_output_dir)
        self.registry = ToolRegistry(self.project_root / "logs" / "assistant" / "tool-audit.jsonl")
        self._register_tools()

    def select_project(self, project_id: str) -> None:
        if project_id and not self.service.get_project(project_id):
            raise ValueError("项目不存在")
        self.current_project_id = project_id

    def _project(self, project_id: str = "") -> str:
        value = project_id or self.current_project_id
        if not value:
            raise ValueError("请先选择项目")
        return value

    def _register_tools(self) -> None:
        register = self.registry.register
        register("list_projects", EmptyArgs, lambda: self.service.list_projects())
        register("get_project_status", ProjectArgs, self.get_project_status)
        register("open_streamlit_page", PageArgs, self.open_streamlit_page)
        register("list_requirements", ProjectArgs, lambda project_id="": self.service.list_requirements(self._project(project_id)))
        register("get_requirement", RequirementArgs, lambda requirement_id, project_id="": self.service.get_requirement(self._project(project_id), requirement_id))
        register("list_generated_cases", ProjectArgs, lambda project_id="": self.service.list_generated_cases(self._project(project_id)))
        register("generate_test_cases", ProjectArgs, self.generate_test_cases)
        register("get_case_review_summary", ProjectArgs, self.get_case_review_summary)
        register("regenerate_single_case", CaseArgs, lambda case_id, feedback="", project_id="": self.service.regenerate_single_case(self._project(project_id), case_id, feedback))
        register("export_project_excel", ProjectArgs, lambda project_id="": {"path": str(self.service.export_excel(self._project(project_id)).resolve())})
        register("parse_document", ParseArgs, lambda path, session_id: self.ingestor.ingest(Path(path), session_id).to_dict())
        register("summarize_document", ParseArgs, self.summarize_document)
        register("compare_documents", CompareArgs, self.compare_documents)
        register("create_docx", DocxExportArgs, lambda **kwargs: self.exporter.create_docx(**kwargs).to_dict())
        register("create_xlsx", XlsxExportArgs, lambda **kwargs: self.exporter.create_xlsx(**kwargs).to_dict())
        register("create_pdf", TextExportArgs, lambda **kwargs: self.exporter.create_pdf(**kwargs).to_dict())
        register("create_txt", TextExportArgs, lambda **kwargs: self.exporter.create_txt(**kwargs).to_dict())
        register("create_markdown", TextExportArgs, lambda **kwargs: self.exporter.create_markdown(**kwargs).to_dict())
        register("list_session_files", SessionArgs, lambda session_id: self.list_session_files(session_id))
        register("open_output_file", OpenPathArgs, lambda path: self.open_output_path(path, reveal=False))
        register("reveal_output_directory", OpenPathArgs, lambda path: self.open_output_path(path, reveal=True))
        register("get_latest_run_status", EmptyArgs, self.get_latest_run_status)
        register("get_error_details", EmptyArgs, self.get_error_details)
        register("cancel_current_task", EmptyArgs, self.cancel_current_task)

    def get_project_status(self, project_id: str = "") -> dict:
        pid = self._project(project_id)
        requirements = self.service.list_requirements(pid)
        cases = self.service.list_generated_cases(pid)
        covered = {str(case.get("requirement_id") or "") for case in cases}
        return {"project": self.service.get_project(pid), "requirements": len(requirements), "generated_cases": len(cases), "requirements_without_cases": sum(1 for row in requirements if str(row.get("requirement_id") or "") not in covered), "review": self.get_case_review_summary(pid)}

    def get_case_review_summary(self, project_id: str = "") -> dict:
        cases = self.service.list_generated_cases(self._project(project_id))
        pending = 0
        for row in cases:
            payload = row.get("case_json") or {}
            if isinstance(payload, str):
                try: payload = json.loads(payload)
                except json.JSONDecodeError: payload = {}
            if payload.get("need_human_confirm") or payload.get("need_human_confirmation") or payload.get("review_status") in {"draft_needs_review", "pending"}: pending += 1
        return {"total": len(cases), "pending_confirmation": pending}

    @staticmethod
    def open_streamlit_page(page: str, base_url: str = "http://127.0.0.1:8501") -> dict:
        return {"page": page, "url": page_url(base_url, page)}

    def list_session_files(self, project_id: str = "") -> list[dict]:
        session = project_id
        folder = (self.exporter.output_root / session).resolve()
        if self.exporter.output_root not in folder.parents or not folder.exists(): return []
        return [{"name": path.name, "path": str(path), "size": path.stat().st_size} for path in folder.iterdir() if path.is_file()]

    def generate_test_cases(self, project_id: str = "") -> ToolResult:
        """Never invent generation parameters; the formal workflow owns them."""
        self._project(project_id)
        return ToolResult(False, "请在现有生成页面确认测试类型、数量和已审核需求后启动；小测不会绕过正式生成参数。", error="confirmation_required")

    def summarize_document(self, path: str, session_id: str) -> dict:
        parsed = self.ingestor.ingest(Path(path), session_id)
        text = parsed.text.strip()
        return {"attachment_id": parsed.attachment_id, "source": parsed.name, "summary": text[:2000], "truncated": len(text) > 2000}

    def compare_documents(self, left_path: str, right_path: str, session_id: str) -> dict:
        left = self.ingestor.ingest(Path(left_path), session_id)
        right = self.ingestor.ingest(Path(right_path), session_id)
        left_lines, right_lines = set(left.text.splitlines()), set(right.text.splitlines())
        return {"left": left.name, "right": right.name, "only_left": sorted(left_lines - right_lines)[:200], "only_right": sorted(right_lines - left_lines)[:200], "common_line_count": len(left_lines & right_lines)}

    def open_output_path(self, path: str, reveal: bool) -> dict:
        import os
        candidate = Path(path).resolve()
        allowed = self.exporter.output_root
        if candidate != allowed and allowed not in candidate.parents:
            raise ValueError("只允许打开助手输出目录内的文件")
        target = candidate.parent if reveal and candidate.is_file() else candidate
        if not target.exists(): raise ValueError("输出路径不存在")
        if os.name != "nt": raise RuntimeError("打开文件仅支持 Windows 桌面会话")
        os.startfile(str(target))
        return {"opened": str(target)}

    def get_latest_run_status(self) -> dict:
        manifests = list((self.project_root / "logs" / "runs").glob("*/manifest.json"))
        if not manifests: return {"status": "idle"}
        latest = max(manifests, key=lambda path: path.stat().st_mtime)
        return json.loads(latest.read_text(encoding="utf-8"))

    def get_error_details(self) -> dict:
        status = self.get_latest_run_status()
        return {"run_id": status.get("run_id", ""), "error": status.get("failure_reason") or status.get("error") or ""}

    def cancel_current_task(self) -> ToolResult:
        return ToolResult(False, "当前测试用例生成后端不支持安全取消；未伪造取消成功。", error="cancellation_not_supported")
