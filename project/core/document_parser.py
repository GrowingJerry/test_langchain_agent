# -*- coding: utf-8 -*-
"""Document parsing helpers for requirement-like inputs."""

from dataclasses import dataclass, field
from pathlib import Path
import re
import tempfile
import warnings
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from models.schemas import RequirementItem


DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
CELL_SEP = "\x07"
PAGE_SEP = "\x0c"


@dataclass
class ParsedDocument:
    """Plain text plus normalized table rows extracted from a document."""

    text: str = ""
    tables: List[List[List[str]]] = field(default_factory=list)


def read_docx_text(path: Path) -> str:
    """Extract visible paragraph and table-cell text from a docx file."""
    with ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    paragraphs: List[str] = []
    for para in root.findall(".//w:p", DOCX_NS):
        text = "".join(t.text or "" for t in para.findall(".//w:t", DOCX_NS)).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _clean_cell(text: str) -> str:
    return re.sub(
        r"\s+", " ", (text or "").replace("\r", "").replace(CELL_SEP, "")
    ).strip()


def _normalize_text(text: str) -> str:
    text = (text or "").replace(CELL_SEP, "\n").replace(PAGE_SEP, "\n")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.splitlines():
        s = re.sub(r"\s+", " ", line).strip()
        if s:
            lines.append(s)
    return "\n".join(lines)


def read_docx_tables(path: Path) -> List[List[List[str]]]:
    """Extract Word tables from a docx file as row/cell strings."""
    with ZipFile(path) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    tables: List[List[List[str]]] = []
    for tbl in root.findall(".//w:tbl", DOCX_NS):
        rows: List[List[str]] = []
        for tr in tbl.findall("./w:tr", DOCX_NS):
            row = []
            for tc in tr.findall("./w:tc", DOCX_NS):
                texts = [t.text or "" for t in tc.findall(".//w:t", DOCX_NS)]
                row.append(_clean_cell("".join(texts)))
            if any(row):
                rows.append(row)
        if rows:
            tables.append(rows)
    return tables


def _read_doc_with_word_com(path: Path) -> ParsedDocument:
    """Read legacy .doc via Word/WPS COM when available on Windows."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = None
    doc = None
    try:
        app = win32com.client.Dispatch("Word.Application")
        app.Visible = False
        doc = app.Documents.Open(str(path.resolve()), ReadOnly=True)
        text = doc.Content.Text
        tables: List[List[List[str]]] = []
        for ti in range(1, doc.Tables.Count + 1):
            table = doc.Tables(ti)
            rows: List[List[str]] = []
            for ri in range(1, table.Rows.Count + 1):
                cells = []
                for ci in range(1, table.Columns.Count + 1):
                    try:
                        cells.append(_clean_cell(table.Cell(ri, ci).Range.Text))
                    except (AttributeError, TypeError, pythoncom.com_error):
                        cells.append("")
                if any(cells):
                    rows.append(cells)
            if rows:
                tables.append(rows)
        return ParsedDocument(text=_normalize_text(text), tables=tables)
    finally:
        if doc is not None:
            doc.Close(False)
        if app is not None:
            app.Quit()
        pythoncom.CoUninitialize()


def _read_doc_by_com_text_export(path: Path) -> ParsedDocument:
    """Fallback COM path: save legacy .doc as UTF-8 text and read it back."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = None
    doc = None
    tmp_path = Path(tempfile.gettempdir()) / f"{path.stem}_codex_doc_extract.txt"
    try:
        app = win32com.client.Dispatch("Word.Application")
        app.Visible = False
        doc = app.Documents.Open(str(path.resolve()), ReadOnly=True)
        doc.SaveAs2(str(tmp_path), FileFormat=7)
        return ParsedDocument(
            text=_normalize_text(tmp_path.read_text(encoding="gbk", errors="ignore"))
        )
    finally:
        if doc is not None:
            doc.Close(False)
        if app is not None:
            app.Quit()
        pythoncom.CoUninitialize()


def _read_doc_binary_fallback(path: Path) -> ParsedDocument:
    """Last-resort extraction for old OLE .doc files without Word COM."""
    data = path.read_bytes()
    chunks = []
    for encoding in ("utf-16le", "gb18030", "utf-8"):
        decoded = data.decode(encoding, errors="ignore")
        chunks.extend(
            re.findall(r"[\u4e00-\u9fffA-Za-z0-9，。；：、（）《》\-_/]{4,}", decoded)
        )
    seen = set()
    lines = []
    for chunk in chunks:
        s = _clean_cell(chunk)
        if s and s not in seen:
            seen.add(s)
            lines.append(s)
    return ParsedDocument(text="\n".join(lines))


def read_word_document(path: Path) -> ParsedDocument:
    """Read txt/docx/doc into normalized text and table rows."""
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return ParsedDocument(
            text=_normalize_text(path.read_text(encoding="utf-8", errors="replace"))
        )
    if suffix == ".docx":
        return ParsedDocument(
            text=_normalize_text(read_docx_text(path)), tables=read_docx_tables(path)
        )
    if suffix == ".doc":
        try:
            return _read_doc_with_word_com(path)
        except Exception as com_error:
            warnings.warn(
                f"Word COM parsing failed; trying text export: {type(com_error).__name__}: {com_error}",
                RuntimeWarning,
                stacklevel=2,
            )
            try:
                return _read_doc_by_com_text_export(path)
            except Exception as export_error:
                warnings.warn(
                    f"Word text export failed; using binary fallback: {type(export_error).__name__}: {export_error}",
                    RuntimeWarning,
                    stacklevel=2,
                )
                return _read_doc_binary_fallback(path)
    raise ValueError(f"Unsupported document type: {suffix}")


def _next_req_id(index: int, prefix: str = "REQ") -> str:
    return f"{prefix}-{index:03d}"


def _looks_like_doc_note(line: str) -> bool:
    s = line.strip()
    return (
        not s
        or s.startswith("注")
        or s.startswith("//示例")
        or s.startswith("示例")
        or s.startswith("例：")
        or s.startswith("相关说明")
    )


def _looks_like_heading(line: str) -> bool:
    s = line.strip()
    if re.match(r"^\d+(?:\.\d+)*\s+\S+", s):
        return True
    headings = (
        "范围",
        "标识",
        "系统概述",
        "文档概述",
        "引用文档",
        "相关现状说明",
        "业务需求",
        "业务需求概述",
        "需求分类描述",
        "需求优先级",
        "非功能性需求",
        "性能需求",
        "技术需求",
        "安全性需求",
        "设计约束",
        "接口需求",
        "其他需求",
        "其他需说明的情况",
        "附录",
    )
    return s in headings


def _requirement_id_like(text: str) -> bool:
    return bool(
        re.fullmatch(
            r"[A-Z]{2,}[A-Z0-9_-]*\d{2,}[A-Z0-9_-]*|\d+(?:\.\d+){2,}", text.strip()
        )
    )


def _extract_requirements_from_tables(tables: List[List[List[str]]]) -> Dict[str, str]:
    """Extract requirement id/name pairs from summary and priority tables."""
    reqs: Dict[str, str] = {}
    example_names = {"预订管理", "房间预订", "房间退订", "客户登记"}
    for table in tables:
        header_idx = -1
        id_col = -1
        name_col = -1
        for idx, row in enumerate(table):
            joined = " ".join(row)
            if "优先级" in joined:
                continue
            if (
                "用户需求标识" in joined or "需求标识" in joined
            ) and "需求名称" in joined:
                header_idx = idx
                for ci, cell in enumerate(row):
                    if "标识" in cell or "编号" in cell:
                        id_col = ci
                    if "名称" in cell:
                        name_col = ci
                break
        if header_idx < 0 or id_col < 0:
            continue
        for row in table[header_idx + 1 :]:
            rid = row[id_col].strip() if id_col < len(row) else ""
            name = row[name_col].strip() if 0 <= name_col < len(row) else ""
            if name in example_names:
                continue
            if _requirement_id_like(rid) and name and "需求名称" not in name:
                reqs.setdefault(rid, name)
    return reqs


def _split_sections(text: str) -> List[Tuple[str, str]]:
    """Split normalized document text into heading/content sections."""
    sections: List[Tuple[str, str]] = []
    current_title = ""
    current_lines: List[str] = []
    for line in text.splitlines():
        s = line.strip()
        if _looks_like_doc_note(s):
            continue
        if _looks_like_heading(s):
            if current_title or current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = s
            current_lines = []
        else:
            current_lines.append(s)
    if current_title or current_lines:
        sections.append((current_title, "\n".join(current_lines).strip()))
    return sections


def _detail_for_requirement(
    rid: str, name: str, sections: List[Tuple[str, str]]
) -> str:
    matched = []
    for title, body in sections:
        haystack = f"{title}\n{body}"
        if rid in haystack or (name and name in haystack):
            matched.append(haystack)
    text = "\n".join(matched)
    if not text:
        text = f"{rid} {name}"
    return re.sub(r"\n{2,}", "\n", text).strip()


def _extract_nonfunctional_sections(sections: List[Tuple[str, str]]) -> Dict[str, str]:
    ids = {
        "性能需求": "NFR-PERF",
        "技术需求": "NFR-TECH",
        "安全性需求": "NFR-SEC",
        "设计约束": "NFR-CONSTRAINT",
        "接口需求": "NFR-IF",
        "其他需求": "NFR-OTHER",
    }
    out: Dict[str, str] = {}
    placeholder_words = (
        "描述用户",
        "描述软件",
        "说明系统",
        "说明支持性",
        "说明未来",
        "如有其它",
        "如果用户对某些",
        "房间预订",
        "注：",
    )
    for title, body in sections:
        rid = ids.get(title)
        if not rid or not body:
            continue
        useful = [
            line
            for line in body.splitlines()
            if not any(w in line for w in placeholder_words)
        ]
        if useful:
            out[rid] = f"{title}\n" + "\n".join(useful)
    return out


def _is_template_instruction_block(text: str) -> bool:
    markers = (
        "对本项目的业务需求进行概括性描述",
        "对用户需求点分类方式建议采用",
        "每个章节“用户需求标识",
        "优先级分为高、中、低三级",
        "分系统或部门分别描述",
        "功能需求描述",
        "描述用户从",
        "如果用户对",
        "描述软件的运行环境",
        "说明系统对",
        "说明未来软件",
        "如有其它需求",
        "本条应",
        "本章应",
        "需要删除文档中所有的注",
        "可按不同方式进行用户需求分类",
    )
    return any(marker in text for marker in markers)


def _items_from_lines(
    lines: List[str],
    test_object: str = "",
    id_prefix: str = "REQ",
) -> List[RequirementItem]:
    items: List[RequirementItem] = []
    for idx, line in enumerate(lines, 1):
        items.append(
            RequirementItem(
                requirement_id=_next_req_id(idx, id_prefix),
                requirement_text=line,
                test_object=test_object,
                source_type="requirement",
            )
        )
    return items


def parse_user_requirement_document(
    path: Path,
    test_object: str = "",
    id_prefix: str = "REQ",
) -> List[RequirementItem]:
    """Parse the user-requirement-spec template by tables and sections.

    This targets documents shaped like data/用户需求说明书.doc: requirement
    summary tables, "需求分类描述", and non-functional requirement chapters.
    """
    doc = read_word_document(path)
    sections = _split_sections(doc.text)
    table_reqs = _extract_requirements_from_tables(doc.tables)
    nf_reqs = _extract_nonfunctional_sections(sections)

    items: List[RequirementItem] = []
    for rid, name in table_reqs.items():
        items.append(
            RequirementItem(
                requirement_id=rid,
                requirement_text=_detail_for_requirement(rid, name, sections),
                test_object=test_object,
                source_type="requirement",
            )
        )
    for rid, text in nf_reqs.items():
        items.append(
            RequirementItem(
                requirement_id=rid,
                requirement_text=text,
                test_object=test_object,
                source_type="requirement",
            )
        )

    if items:
        return items

    candidates = []
    for title, body in sections:
        if title in ("需求分类描述", "业务需求", "业务需求概述") or title.endswith(
            "需求"
        ):
            merged = "\n".join([title, body]).strip()
            if len(merged) > 12 and not _is_template_instruction_block(merged):
                candidates.append(merged)
    return _items_from_lines(candidates, test_object=test_object, id_prefix=id_prefix)


def parse_docx_requirements(
    path: Path,
    test_object: str = "",
    id_prefix: str = "REQ",
) -> List[RequirementItem]:
    """Parse requirement items from a docx file."""
    return parse_user_requirement_document(
        path, test_object=test_object, id_prefix=id_prefix
    )


def parse_requirement_file(path: Path, test_object: str = "") -> List[RequirementItem]:
    """Parse txt, xlsx/xls, docx, or legacy doc requirement files."""
    suffix = path.suffix.lower()
    if suffix == ".txt":
        from core.requirement_parser import parse_txt_file

        return parse_txt_file(path, test_object=test_object)
    if suffix in (".xlsx", ".xls"):
        from core.requirement_parser import parse_excel_requirements

        return parse_excel_requirements(path)
    if suffix in (".docx", ".doc"):
        return parse_user_requirement_document(path, test_object=test_object)
    raise ValueError(f"Unsupported requirement file type: {suffix}")
