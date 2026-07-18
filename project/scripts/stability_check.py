# -*- coding: utf-8 -*-
"""End-to-end stability check using temporary project storage."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

import core.project_manager as project_manager_module
from core.advanced_case_generator import AdvancedCaseGenerator
from core.context_builder import ContextBuilder
from core.document_ingestor import save_and_ingest_document
from core.project_document_exporter import (
    export_project_excel,
    export_project_markdown,
    export_project_word,
)
from core.project_kb import search_project_chunks
from core.project_manager import ProjectManager
from core.project_profile_extractor import extract_project_profile
from core.requirement_extractor import extract_requirements_from_chunks
from core.scenario_card_extractor import extract_and_save_scenario_cards
from core.visual_client import OllamaVisualClient

ROOT = Path(__file__).resolve().parents[1]


SAMPLE_TEXT = """项目名称：端到端稳定性验证项目
被测系统：订单处理系统。
当用户提交订单时，系统应接收订单数据并生成订单编号。
系统应提供 HTTP API 接口接收外部订单报文。
订单处理失败时，系统应记录错误日志并返回失败状态。
"""


def _make_pdf_bytes() -> bytes:
    """Create a tiny copyable-text PDF when PyMuPDF is installed."""
    try:
        import fitz
    except ImportError:
        return b"%PDF-1.4\n%%EOF\n"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PDF需求：系统应支持上传PDF资料并解析可复制文本。")
    data = doc.tobytes()
    doc.close()
    return data


def _make_png(path: Path) -> None:
    """Write a minimal 1x1 PNG for upload and base64 checks."""
    path.write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
            "1f15c4890000000a49444154789c6360000002000150a0f5a90000000049454e44ae426082"
        )
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sixqa-stability-") as temp:
        root = Path(temp)
        project_manager_module.PROJECT_OUTPUT_ROOT = root / "projects"
        manager = ProjectManager(root / "workspace.db")

        project = manager.create_project("端到端稳定性验证项目", "临时稳定性检查")
        project_id = project["project_id"]

        txt_result = save_and_ingest_document(
            manager,
            project_id,
            "requirements.txt",
            SAMPLE_TEXT.encode("utf-8"),
        )
        assert txt_result["chunk_count"] > 0, "TXT 文档未生成知识库 chunk"

        pdf_result = save_and_ingest_document(
            manager,
            project_id,
            "requirements.pdf",
            _make_pdf_bytes(),
        )
        assert "warning" in pdf_result, "PDF 入库结果缺少 warning 字段"

        chunks = manager.list_chunks(project_id, limit=100)
        assert chunks, "知识库没有可检索 chunk"
        assert search_project_chunks(manager, project_id, "订单 接口", 5), (
            "知识库检索无结果"
        )

        text = "\n".join(str(c.get("content") or "") for c in chunks)
        profile = extract_project_profile(
            text, project_name_hint=project["project_name"], use_ollama=False
        )
        manager.save_profile(project_id, profile)
        assert manager.get_profile(project_id), "项目画像保存失败"

        requirements = extract_requirements_from_chunks(chunks)
        if not requirements:
            requirements = [
                {
                    "requirement_id": "REQ-001",
                    "title": "订单接收",
                    "description": "系统应接收订单数据并生成订单编号。",
                    "category": "功能需求",
                    "source_document": txt_result["filename"],
                    "source_chunk_id": chunks[0]["chunk_id"],
                }
            ]
        manager.replace_requirements(project_id, requirements)
        assert manager.list_requirements(project_id), "需求抽取或保存失败"

        cards = extract_and_save_scenario_cards(manager, project_id, use_ollama=False)
        assert cards, "场景卡生成失败"

        image_path = manager.project_dir(project_id) / "visual_assets" / "screen.png"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        _make_png(image_path)
        asset_id = manager.save_project_asset(
            project_id,
            {
                "asset_type": "image",
                "file_path": str(image_path),
                "source_document": "screen.png",
            },
        )
        evidence_id = manager.save_visual_evidence(
            project_id,
            asset_id,
            {
                "image_type": "ui_screenshot",
                "visible_text": ["提交订单"],
                "possible_functions": ["订单提交"],
                "possible_test_points": ["提交按钮可用性"],
                "risk_points": [],
                "confidence": 0.8,
                "need_human_confirm": False,
                "raw_response": "{}",
            },
        )
        assert manager.list_project_assets(project_id), "视觉资产保存失败"
        assert manager.get_visual_evidence_by_asset(project_id, asset_id), (
            "视觉证据保存失败"
        )

        visual_client = OllamaVisualClient()
        assert visual_client.encode_image_to_base64(image_path)["ok"], (
            "图片 base64 编码失败"
        )
        unavailable = visual_client.generate_json_with_images(
            "{}", image_paths=[image_path]
        )
        assert "ok" in unavailable and (
            "data" in unavailable or "error" in unavailable
        ), "视觉客户端返回结构异常"

        requirement_id = manager.list_requirements(project_id)[0]["requirement_id"]
        context = ContextBuilder(manager).build(
            project_id, requirement_id, "功能测试", persist=True
        )
        assert context["visual_evidence"], "视觉证据未进入生成上下文"
        assert (
            context["evidence_context"]["text_document_evidence"]
            is context["related_chunks"]
        ), "上下文证据结构异常"

        result = AdvancedCaseGenerator(manager).generate(
            project_id,
            requirement_id,
            "功能测试",
            use_ollama=False,
            use_history=False,
        )
        case = result["case"]
        case["evidence_sources"] = [evidence_id]
        manager.save_generated_case(
            project_id,
            case,
            case.get("generation_run_id", ""),
            context.get("related_chunks", []),
        )
        assert manager.list_generated_cases(project_id), "测试用例保存失败"
        assert manager.list_quality_scores(project_id), "质量评分保存失败"
        assert manager.list_trace_sources(project_id), "结果追溯保存失败"

        excel_path = export_project_excel(manager, project_id)
        word_path = export_project_word(manager, project_id)
        markdown_path = export_project_markdown(manager, project_id)
        assert excel_path.is_file(), "Excel 导出失败"
        assert word_path.is_file(), "Word 导出失败"
        assert markdown_path.is_file(), "Markdown 导出失败"

        print("stability-check: OK")
        print(
            json.dumps(
                {
                    "project_id": project_id,
                    "txt_chunks": txt_result["chunk_count"],
                    "pdf_chunks": pdf_result["chunk_count"],
                    "pdf_warning": pdf_result.get("warning", ""),
                    "requirements": len(manager.list_requirements(project_id)),
                    "scenario_cards": len(cards),
                    "visual_evidence": len(
                        manager.list_visual_evidence_by_project(project_id)
                    ),
                    "generated_cases": len(manager.list_generated_cases(project_id)),
                    "trace_sources": len(manager.list_trace_sources(project_id)),
                    "exports": [excel_path.name, word_path.name, markdown_path.name],
                    "vision_client_ok": bool(unavailable.get("ok")),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
