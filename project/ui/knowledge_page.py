"""Project documents and isolated knowledge-base page."""

import json
from typing import Any, Optional

import streamlit as st

from core.requirement_extractor import classify_requirement

IMAGE_TYPES = ["png", "jpg", "jpeg", "webp"]


def _table_value(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return value


def _table_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: _table_value(value) for key, value in row.items()} for row in rows]


def _render_visual_evidence_area(service: Any, project_id: str) -> None:
    with st.expander("多模态资料 / 图片证据抽取"):
        st.info("视觉识别结果仅作为测试设计证据，需要人工确认。")
        images = st.file_uploader(
            "上传截图、流程图、扫描页或设备界面图",
            type=IMAGE_TYPES,
            accept_multiple_files=True,
            key=f"visual_assets_{project_id}",
        )
        if st.button(
            "上传并抽取视觉证据",
            disabled=not images,
            key=f"extract_visual_{project_id}",
        ):
            for image in images or []:
                result = service.process_visual_upload(
                    project_id, image.name, image.getvalue()
                )
                if result.get("ok"):
                    st.success(f"{image.name} 已保存视觉证据：{result['evidence_id']}")
                    st.json(result.get("data") or {})
                else:
                    st.warning(f"{image.name} 视觉证据抽取失败：{result.get('error')}")
        assets = service.list_project_assets(project_id)
        evidence = service.list_visual_evidence_by_project(project_id)
        if assets:
            st.subheader("当前项目视觉资产")
            st.dataframe(_table_rows(assets), hide_index=True, use_container_width=True)
        if evidence:
            st.subheader("当前项目视觉证据")
            st.dataframe(
                _table_rows(evidence), hide_index=True, use_container_width=True
            )


def render_knowledge_page(
    service: Any, project_id: Optional[str], case_library: Any, top_k: int
) -> None:
    st.header("文档与知识库")
    if not project_id:
        st.info("请先在项目工作台创建项目。")
        return
    files = st.file_uploader(
        "上传项目说明书、需求、接口、标准等资料",
        type=["txt", "md", "docx", "pdf"],
        accept_multiple_files=True,
        key=f"docs_{project_id}",
    )
    if st.button(
        "上传、解析并入库",
        disabled=not files,
        type="primary",
        key=f"ingest_{project_id}",
    ):
        for item in files or []:
            try:
                result = service.ingest_document(project_id, item.name, item.getvalue())
                message = f"{result['filename']}：{result['chunk_count']} 个片段。{result.get('warning', '')}"
                (st.warning if result.get("warning") else st.success)(message)
            except ValueError as exc:
                st.error(f"{item.name} 上传失败：{exc}")
        st.rerun()
    docs = service.list_documents(project_id)
    if docs:
        st.subheader("项目文档")
        st.dataframe(_table_rows(docs), hide_index=True, use_container_width=True)
        selected = st.selectbox(
            "文档维护",
            docs,
            format_func=lambda row: row["filename"],
            key=f"maintain_doc_{project_id}",
        )
        if st.button("删除选中文档及片段", key=f"delete_doc_{project_id}"):
            service.delete_document(project_id, selected["document_id"])
            st.success("资料记录与片段已删除，原始上传文件保留。")
            st.rerun()
    with st.expander("手动补充需求文本"):
        manual = st.text_area("需求文本（每行一条）", key=f"manual_req_{project_id}")
        if st.button("加入当前项目需求", key=f"add_manual_req_{project_id}"):
            sequence = len(service.list_requirements(project_id)) + 1
            for line in [part.strip() for part in manual.splitlines() if part.strip()]:
                service.upsert_requirement(
                    project_id,
                    {
                        "requirement_id": f"REQ-M-{sequence:03d}",
                        "title": line[:48],
                        "description": line,
                        "category": classify_requirement(line),
                        "source_document": "manual_input",
                    },
                )
                sequence += 1
            st.success("手动需求已加入当前项目。")
    with st.expander("查看知识片段与检索调试"):
        chunks = service.list_chunks(project_id, limit=500)
        st.caption(f"当前项目显示 {len(chunks)} 个 chunks；检索不会跨项目。")
        if chunks:
            st.dataframe(_table_rows(chunks), hide_index=True, use_container_width=True)
        query = st.text_input("检索关键词", key=f"kb_query_{project_id}")
        if st.button("检索当前项目知识库", key=f"kb_search_{project_id}"):
            st.dataframe(
                _table_rows(service.search_documents(project_id, query, top_k)),
                hide_index=True,
                use_container_width=True,
            )
    _render_visual_evidence_area(service, project_id)
    with st.expander("历史用例库（仅作格式和方法参考）"):
        if case_library is None:
            st.info("历史用例库未启用，请在系统设置中开启。")
        else:
            upload = st.file_uploader(
                "导入 Excel / CSV / JSON",
                type=["xlsx", "csv", "json"],
                key=f"history_upload_{project_id}",
            )
            if upload and st.button(
                "导入历史用例库", key=f"import_history_{project_id}"
            ):
                target = service.save_history_upload(
                    project_id, upload.name, upload.getvalue()
                )
                count = (
                    case_library.import_excel(target)
                    if target.suffix.lower() == ".xlsx"
                    else (
                        case_library.import_csv(target)
                        if target.suffix.lower() == ".csv"
                        else case_library.import_json(target)
                    )
                )
                st.success(
                    f"已导入 {count} 条，历史库共 {case_library.count_cases()} 条。"
                )
    st.info("资料解析完成后，请进入“智能生成”抽取画像、需求和场景卡片。")
