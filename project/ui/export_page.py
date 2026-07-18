"""Unified project export center."""

from io import BytesIO
from pathlib import Path
import streamlit as st
from openpyxl import Workbook


def _xlsx_bytes(rows, sheet_name: str) -> bytes:
    buffer = BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name[:31]
    headers = list(rows[0].keys()) if rows else ["empty"]
    sheet.append(headers)
    for row in rows:
        sheet.append(
            [
                "" if row.get(header) is None else str(row.get(header))
                for header in headers
            ]
        )
    workbook.save(buffer)
    return buffer.getvalue()


def render_export_page(service, project_id) -> None:
    st.header("导出中心")
    if not project_id:
        st.info("请先选择项目。")
        return
    rows = service.export_rows(project_id)
    excel_key = f"export_excel_{project_id}"
    word_key = f"export_word_{project_id}"
    markdown_key = f"export_markdown_{project_id}"
    st.caption(
        f"可导出需求 {len(rows['requirements'])} 条、用例 {len(rows['test_cases'])} 条、来源 {len(rows['trace_sources'])} 条。"
    )
    c1, c2, c3 = st.columns(3)
    if c1.button("生成项目级 Excel", key=f"build_excel_{project_id}"):
        st.session_state[excel_key] = str(service.export_excel(project_id))
    if c2.button("生成项目级 Word 初稿", key=f"build_word_{project_id}"):
        try:
            st.session_state[word_key] = str(service.export_word(project_id))
        except ImportError as exc:
            st.error(str(exc))
    if c3.button("生成项目级 Markdown", key=f"build_markdown_{project_id}"):
        st.session_state[markdown_key] = str(service.export_markdown(project_id))
    for key, label, mime in [
        (
            excel_key,
            "下载项目级 Excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        (
            word_key,
            "下载 Word 初稿",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (markdown_key, "下载 Markdown", "text/markdown"),
    ]:
        path = Path(st.session_state.get(key, ""))
        if path.is_file():
            st.download_button(
                label, path.read_bytes(), path.name, mime=mime, key=f"download_{key}"
            )
    st.subheader("专项数据表")
    for key, label, sheet in [
        ("test_cases", "导出测试用例表", "测试用例"),
        ("requirement_case_matrix", "导出需求追踪矩阵", "需求追踪矩阵"),
        ("trace_sources", "导出来源追溯表", "来源追溯表"),
    ]:
        st.download_button(
            label,
            _xlsx_bytes(rows[key], sheet),
            f"{project_id}_{key}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_{project_id}_{key}",
        )
    st.info("全部导出完成后，可返回“项目工作台”查看本项目闭环状态。")
