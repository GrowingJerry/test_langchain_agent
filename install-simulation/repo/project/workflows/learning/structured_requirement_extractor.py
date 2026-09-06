# -*- coding: utf-8 -*-
"""Structure-first requirement extraction for formal requirement documents."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from domain.rules.test_types import is_numeric_test_type, normalize_test_type
from infrastructure.documents.document_parser import parse_requirement_file
from workflows.learning.requirement_extractor import extract_requirements_from_chunks

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DOCX_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

REQ_ID_RE = re.compile(
    r"\b(?:REQ|SYS|SRS|URS|IF|PERF|SEC|FUNC|NFR)[A-Z0-9_-]*-\d{2,}\b",
    re.I,
)
SECTION_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:[、.\s]+)(\S.*)$")
FIELD_RE = re.compile(r"^(需求编号|需求标识|标识号|标题|需求名称|测试类型|输入|处理|处理规则|输出|异常处理|性能指标|安全约束|接口参数|优先级|合格性方法|验证方法|可验证性|前置条件|角色)[:：]\s*(.*)$")


def _convert_doc_to_temp_docx(path: Path) -> Path:
    """Convert legacy .doc to a temporary .docx via Word/WPS COM on Windows."""
    import pythoncom
    import win32com.client

    target = Path(tempfile.gettempdir()) / f"{path.stem}_{hashlib.sha1(str(path).encode()).hexdigest()[:8]}.docx"
    pythoncom.CoInitialize()
    app = None
    doc = None
    try:
        app = win32com.client.Dispatch("Word.Application")
        app.Visible = False
        doc = app.Documents.Open(str(path.resolve()), ReadOnly=True)
        doc.SaveAs2(str(target), FileFormat=16)
        return target
    finally:
        if doc is not None:
            doc.Close(False)
        if app is not None:
            app.Quit()
        pythoncom.CoUninitialize()


@dataclass
class DocumentBlock:
    block_id: str
    document_id: str
    block_type: str
    text: str
    style_name: str = ""
    heading_level: Optional[int] = None
    section_number: str = ""
    section_title: str = ""
    section_path: List[str] = field(default_factory=list)
    paragraph_index: Optional[int] = None
    table_index: Optional[int] = None
    row_index: Optional[int] = None
    cell_index: Optional[int] = None
    list_number: str = ""
    list_level: Optional[int] = None
    table_headers: List[str] = field(default_factory=list)
    source_document: str = ""
    page_no: Optional[int] = None
    previous_block_id: str = ""
    next_block_id: str = ""


def _text(element: ET.Element) -> str:
    return re.sub(r"\s+", " ", "".join(t.text or "" for t in element.findall(".//w:t", DOCX_NS))).strip()


def _style_map(zf: ZipFile) -> Dict[str, str]:
    try:
        root = ET.fromstring(zf.read("word/styles.xml"))
    except KeyError:
        return {}
    result: Dict[str, str] = {}
    for style in root.findall(".//w:style", DOCX_NS):
        sid = style.get(f"{W}styleId") or ""
        name = style.find("./w:name", DOCX_NS)
        if sid and name is not None:
            result[sid] = name.get(f"{W}val") or sid
    return result


def _paragraph_meta(p: ET.Element, styles: Dict[str, str]) -> tuple[str, Optional[int], str, Optional[int]]:
    ppr = p.find("./w:pPr", DOCX_NS)
    style_name = ""
    level: Optional[int] = None
    list_number = ""
    list_level: Optional[int] = None
    if ppr is not None:
        pstyle = ppr.find("./w:pStyle", DOCX_NS)
        if pstyle is not None:
            sid = pstyle.get(f"{W}val") or ""
            style_name = styles.get(sid, sid)
            match = re.search(r"(?:Heading|标题)\s*([1-9])", style_name, re.I)
            if match:
                level = int(match.group(1))
        numpr = ppr.find("./w:numPr", DOCX_NS)
        if numpr is not None:
            ilvl = numpr.find("./w:ilvl", DOCX_NS)
            numid = numpr.find("./w:numId", DOCX_NS)
            list_level = int(ilvl.get(f"{W}val") or 0) if ilvl is not None else 0
            list_number = numid.get(f"{W}val") if numid is not None else ""
    return style_name, level, list_number, list_level


def parse_docx_blocks(path: Path, document_id: str = "") -> List[DocumentBlock]:
    """Parse DOCX body order, headings, paragraphs, lists and table rows."""
    doc_id = document_id or hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
    with ZipFile(path) as zf:
        styles = _style_map(zf)
        root = ET.fromstring(zf.read("word/document.xml"))
    body = root.find("./w:body", DOCX_NS)
    if body is None:
        return []
    blocks: List[DocumentBlock] = []
    headings: Dict[int, str] = {}
    paragraph_index = 0
    table_index = 0
    section_number = ""
    section_title = ""

    def current_path() -> List[str]:
        return [headings[k] for k in sorted(headings) if headings[k]]

    def add(block: DocumentBlock) -> None:
        if blocks:
            block.previous_block_id = blocks[-1].block_id
            blocks[-1].next_block_id = block.block_id
        blocks.append(block)

    for child in body:
        if child.tag == f"{W}p":
            text = _text(child)
            if not text:
                continue
            paragraph_index += 1
            style_name, level, list_number, list_level = _paragraph_meta(child, styles)
            sec_match = SECTION_RE.match(text)
            if level is None and sec_match and len(sec_match.group(1).split(".")) <= 4 and len(text) <= 120:
                level = len(sec_match.group(1).split("."))
            if level:
                title = sec_match.group(2) if sec_match else text
                section_number = sec_match.group(1) if sec_match else section_number
                section_title = title
                headings = {k: v for k, v in headings.items() if k < level}
                headings[level] = f"{section_number} {title}".strip()
                block_type = "heading"
            elif "注释" in style_name or style_name.lower() == "comment text":
                block_type = "comment"
            elif list_number:
                block_type = "list"
            else:
                block_type = "paragraph"
            add(DocumentBlock(
                block_id=f"{doc_id}-B{len(blocks)+1:05d}", document_id=doc_id,
                block_type=block_type, text=text, style_name=style_name,
                heading_level=level, section_number=section_number,
                section_title=section_title, section_path=current_path(),
                paragraph_index=paragraph_index, list_number=list_number,
                list_level=list_level, source_document=path.name,
            ))
        elif child.tag == f"{W}tbl":
            table_index += 1
            rows: List[List[str]] = []
            for tr in child.findall("./w:tr", DOCX_NS):
                cells = [_text(tc) for tc in tr.findall("./w:tc", DOCX_NS)]
                if any(cells):
                    rows.append(cells)
            headers = rows[0] if rows else []
            for ri, row in enumerate(rows, 1):
                pairs = []
                for ci, cell in enumerate(row):
                    head = headers[ci] if ci < len(headers) else f"列{ci+1}"
                    if ri == 1:
                        pairs.append(cell)
                    elif cell:
                        pairs.append(f"{head}: {cell}")
                text = "；".join(x for x in pairs if x)
                if text:
                    add(DocumentBlock(
                        block_id=f"{doc_id}-B{len(blocks)+1:05d}", document_id=doc_id,
                        block_type="table_header" if ri == 1 else "table_row",
                        text=text, section_number=section_number,
                        section_title=section_title, section_path=current_path(),
                        table_index=table_index, row_index=ri, cell_index=None,
                        table_headers=headers, source_document=path.name,
                    ))
    return blocks


def _is_template_instruction(text: str, block: Optional[DocumentBlock] = None) -> bool:
    s = re.sub(r"\s+", "", text or "")
    if not s:
        return True
    if block and block.block_type == "comment":
        return True
    markers = ("本条应", "本章应", "在形成最后文档时", "注：", "注1：", "注2：", "填写说明", "删除文档中所有的注", "示例：")
    if s.startswith(("注:", "注：", "注1", "注2")) or any(m in s for m in markers):
        return True
    placeholder = len(re.findall(r"XX|xx|XXXX|20xx年xx月|××", s, re.I))
    return placeholder > 0 and len(re.sub(r"XX|xx|XXXX|20xx年xx月|××|\W", "", s, flags=re.I)) < 12


def classify_document(blocks: List[DocumentBlock], filename: str = "") -> Dict[str, Any]:
    text = "\n".join(b.text for b in blocks[:200])
    name_text = f"{filename}\n{text}"
    if "接口" in name_text and "需求" in name_text:
        doc_type = "接口需求文档"
    elif "软件需求规格" in name_text:
        doc_type = "软件需求规格说明书"
    elif "用户需求" in name_text:
        doc_type = "用户需求说明书"
    else:
        doc_type = "普通技术资料"
    useful = [b for b in blocks if b.block_type != "heading" and not _is_template_instruction(b.text, b)]
    template = [b for b in blocks if _is_template_instruction(b.text, b)]
    if not useful and template:
        state = "空白模板"
    elif useful and template:
        state = "模板与实际内容混合的文档"
    elif useful:
        state = "已填写的正式文档"
    else:
        state = "普通技术资料"
    return {"document_type": doc_type, "content_state": state}


def _stable_id(document_id: str, section_path: Iterable[str], text: str) -> str:
    digest = hashlib.sha1(("\x1f".join(section_path) + "\x1f" + text).encode("utf-8")).hexdigest()[:10].upper()
    return f"REQ-{document_id[:6].upper()}-{digest}"


def _field_bucket(field_name: str) -> str:
    return {
        "角色": "actors", "前置条件": "preconditions", "输入": "inputs",
        "处理": "processing_rules", "处理规则": "processing_rules", "输出": "outputs",
        "异常处理": "exception_rules", "性能指标": "performance_constraints",
        "安全约束": "security_constraints", "接口参数": "interface_constraints",
        "合格性方法": "acceptance_criteria", "可验证性": "acceptance_criteria",
    }.get(field_name, "")


def infer_test_type(req: Dict[str, Any], explicit: str = "") -> Dict[str, Any]:
    reasons: List[str] = []
    candidates: List[str] = []
    raw = explicit.strip()
    normalized = normalize_test_type(raw)
    if normalized:
        return {"recommended_test_type": normalized, "alternative_test_types": [], "test_type_confidence": 0.99, "test_type_reasons": [f"原文明确填写测试类型：{raw}"], "need_human_confirm": False}
    need_confirm = False
    if is_numeric_test_type(raw):
        need_confirm = True
        reasons.append(f"原文测试类型为数字 {raw}，未找到图例或确认映射")

    rid = str(req.get("requirement_id") or "").upper()
    section = " ".join(req.get("section_path") or [])
    text = "\n".join(str(req.get(k) or "") for k in ("title", "description"))
    for k in ("inputs", "processing_rules", "outputs", "exception_rules", "performance_constraints", "interface_constraints", "security_constraints"):
        text += "\n" + "\n".join(req.get(k) or [])
    haystack = f"{rid}\n{section}\n{text}"

    signals = [
        ("性能测试", ("PERF", "性能", "响应时间", "吞吐", "并发", "容量", "资源占用", "时延", "≤", ">=", "不高于", "不低于")),
        ("安全性测试", ("SEC", "安全", "登录", "权限", "认证", "加密", "审计", "保密", "入侵")),
        ("接口测试", ("IF", "接口", "协议", "报文", "API", "参数", "数据格式", "通信")),
        ("可靠性测试", ("可靠", "恢复", "连续运行", "MTBF", "容错", "故障")),
        ("兼容性测试", ("兼容", "适配", "操作系统", "数据库", "浏览器", "硬件平台", "版本")),
        ("安装测试", ("安装", "升级", "卸载", "部署")),
        ("界面/易用性测试", ("界面", "布局", "操作流程", "提示信息", "易用")),
        ("静态测试", ("代码规范", "静态分析", "代码审查")),
        ("环境适应性测试", ("温度", "湿度", "冲击", "电磁兼容", "环境适应")),
        ("功能测试", ("FUNC", "功能", "业务流程", "输入", "处理", "输出", "应", "必须", "支持")),
    ]
    scores: Dict[str, int] = {}
    for label, words in signals:
        score = sum(1 for word in words if word and word in haystack)
        if score:
            scores[label] = score
            reasons.append(f"命中{label}线索：{score}项")
    if req.get("performance_constraints"):
        scores["性能测试"] = scores.get("性能测试", 0) + 4
        reasons.append("需求包含结构化性能指标")
    if req.get("interface_constraints"):
        scores["接口测试"] = scores.get("接口测试", 0) + 4
        reasons.append("需求包含结构化接口参数")
    if req.get("security_constraints"):
        scores["安全性测试"] = scores.get("安全性测试", 0) + 4
        reasons.append("需求包含结构化安全约束")
    if not scores:
        need_confirm = True
        scores["功能测试"] = 1
        reasons.append("未发现明确分类线索，默认功能测试并要求人工确认")
    ordered = sorted(scores, key=lambda label: (-scores[label], label))
    candidates = ordered[1:4]
    confidence = min(0.95, 0.45 + scores[ordered[0]] * 0.1)
    return {"recommended_test_type": ordered[0], "alternative_test_types": candidates, "test_type_confidence": confidence, "test_type_reasons": reasons[:8], "need_human_confirm": need_confirm}


def extract_requirements_from_blocks(blocks: List[DocumentBlock]) -> List[Dict[str, Any]]:
    meta = classify_document(blocks, blocks[0].source_document if blocks else "")
    if meta["content_state"] == "空白模板":
        return []
    rows: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    explicit_test_type = ""

    def finish() -> None:
        nonlocal current, explicit_test_type
        if not current:
            return
        current["description"] = "\n".join(current.pop("_description_parts", [])).strip()
        verdict = infer_test_type(current, explicit_test_type)
        current.update(verdict)
        current["need_human_confirm"] = bool(current.get("need_human_confirm") or verdict["need_human_confirm"])
        rows.append(current)
        current = None
        explicit_test_type = ""

    for block in blocks:
        if _is_template_instruction(block.text, block):
            continue
        rid_match = REQ_ID_RE.search(block.text)
        field_match = FIELD_RE.match(block.text)
        starts_new = bool(rid_match) or (block.block_type == "table_row" and any(h in block.text for h in ("需求编号", "需求标识", "标识号")))
        if block.block_type == "heading" and current and block.heading_level and block.heading_level <= int(current.get("_heading_level") or 99):
            finish()
        if starts_new:
            finish()
            rid = rid_match.group(0) if rid_match else ""
            title = block.text
            for name in ("需求编号", "需求标识", "标识号", "需求名称", "标题"):
                m = re.search(fr"{name}[:：]\s*([^；;]+)", block.text)
                if m and name in ("需求编号", "需求标识", "标识号"):
                    rid = m.group(1).strip()
                elif m:
                    title = m.group(1).strip()
            test_type_match = re.search(r"测试类型[:：]\s*([^；;]+)", block.text)
            if test_type_match:
                explicit_test_type = test_type_match.group(1).strip()
            rid = rid or _stable_id(block.document_id, block.section_path, block.text)
            current = {
                "requirement_id": rid, "title": title[:120], "description": "",
                "requirement_type": "functional", "category": "功能需求",
                "section_number": block.section_number, "section_path": block.section_path,
                "test_object": "", "actors": [], "preconditions": [], "inputs": [],
                "processing_rules": [], "outputs": [], "exception_rules": [],
                "performance_constraints": [], "interface_constraints": [],
                "security_constraints": [], "acceptance_criteria": [], "priority": "",
                "verification_method": "", "parent_requirement_ids": [],
                "source_chunk_ids": [block.block_id], "source_documents": [block.source_document],
                "source_evidence": [{"block_id": block.block_id, "text": block.text, "section_path": block.section_path, "table_index": block.table_index, "row_index": block.row_index}],
                "need_human_confirm": False, "missing_information": [],
                "_description_parts": [block.text], "_heading_level": block.heading_level or 99,
            }
        elif current:
            current["source_chunk_ids"].append(block.block_id)
            current["source_evidence"].append({"block_id": block.block_id, "text": block.text, "section_path": block.section_path, "table_index": block.table_index, "row_index": block.row_index})
            if field_match:
                name, value = field_match.group(1), field_match.group(2).strip()
                if name == "测试类型":
                    explicit_test_type = value
                elif name == "优先级":
                    current["priority"] = value
                elif name in ("验证方法", "合格性方法"):
                    current["verification_method"] = value
                elif bucket := _field_bucket(name):
                    current[bucket].append(value or block.text)
                else:
                    current["_description_parts"].append(block.text)
            else:
                current["_description_parts"].append(block.text)
        elif rid_match or ("需求" in " ".join(block.section_path) and any(k in block.text for k in ("应", "必须", "支持", "提供"))):
            current = None
            synthetic = _stable_id(block.document_id, block.section_path, block.text)
            rows.append({
                "requirement_id": rid_match.group(0) if rid_match else synthetic,
                "title": block.text[:80], "description": block.text,
                "requirement_type": "functional", "category": "功能需求",
                "section_number": block.section_number, "section_path": block.section_path,
                "test_object": "", "actors": [], "preconditions": [], "inputs": [],
                "processing_rules": [], "outputs": [], "exception_rules": [],
                "performance_constraints": [], "interface_constraints": [],
                "security_constraints": [], "acceptance_criteria": [], "priority": "",
                "verification_method": "", "parent_requirement_ids": [],
                "source_chunk_ids": [block.block_id], "source_documents": [block.source_document],
                "source_evidence": [{"block_id": block.block_id, "text": block.text, "section_path": block.section_path}],
                "missing_information": [],
                **infer_test_type({"requirement_id": rid_match.group(0) if rid_match else synthetic, "title": block.text, "description": block.text, "section_path": block.section_path}),
            })
    finish()
    return rows


def build_extraction_quality_report(
    blocks: List[DocumentBlock],
    rows: List[Dict[str, Any]],
    *,
    filename: str = "",
    warnings: Optional[List[str]] = None,
    fallback_used: bool = False,
) -> Dict[str, Any]:
    meta = classify_document(blocks, filename) if blocks else {
        "document_type": "普通技术资料",
        "content_state": "普通技术资料",
    }
    original_id_count = sum(1 for row in rows if REQ_ID_RE.search(str(row.get("requirement_id") or "")))
    explicit_count = sum(
        1
        for row in rows
        if any("原文明确" in str(reason) for reason in row.get("test_type_reasons") or [])
    )
    low_confidence = [
        {
            "requirement_id": row.get("requirement_id", ""),
            "title": row.get("title", ""),
            "confidence": row.get("test_type_confidence", 0),
            "recommended_test_type": row.get("recommended_test_type", ""),
        }
        for row in rows
        if float(row.get("test_type_confidence") or 0) < 0.7 or row.get("need_human_confirm")
    ]
    table_indexes = {b.table_index for b in blocks if b.table_index}
    return {
        "document_type": meta["document_type"],
        "content_state": meta["content_state"],
        "section_count": sum(1 for b in blocks if b.block_type == "heading"),
        "requirement_count": len(rows),
        "original_id_count": original_id_count,
        "generated_id_count": max(0, len(rows) - original_id_count),
        "explicit_test_type_count": explicit_count,
        "recommended_test_type_count": sum(1 for row in rows if row.get("recommended_test_type")),
        "need_human_confirm_count": sum(1 for row in rows if row.get("need_human_confirm")),
        "filtered_template_instruction_count": sum(1 for b in blocks if _is_template_instruction(b.text, b)),
        "unparsed_table_count": 0,
        "table_count": len(table_indexes),
        "low_confidence_requirements": low_confidence,
        "warnings": list(warnings or []) + (["已使用普通文本兜底抽取"] if fallback_used else []),
    }


def _normalize_requirement_row(
    row: Dict[str, Any],
    *,
    source_document: str,
) -> Dict[str, Any]:
    """Fill the rich preview fields for template and plain-text fallbacks."""
    normalized = dict(row)
    description = str(
        normalized.get("description")
        or normalized.get("requirement_text")
        or ""
    ).strip()
    title = str(normalized.get("title") or "").strip()
    normalized.update({
        "title": title or (description.splitlines()[0][:120] if description else ""),
        "description": description,
        "requirement_type": normalized.get("requirement_type") or "functional",
        "category": normalized.get("category") or "功能需求",
        "section_number": normalized.get("section_number") or "",
        "section_path": list(normalized.get("section_path") or []),
        "test_object": normalized.get("test_object") or "",
        "actors": list(normalized.get("actors") or []),
        "preconditions": list(normalized.get("preconditions") or []),
        "inputs": list(normalized.get("inputs") or []),
        "processing_rules": list(normalized.get("processing_rules") or []),
        "outputs": list(normalized.get("outputs") or []),
        "exception_rules": list(normalized.get("exception_rules") or []),
        "performance_constraints": list(
            normalized.get("performance_constraints") or []
        ),
        "interface_constraints": list(
            normalized.get("interface_constraints") or []
        ),
        "security_constraints": list(
            normalized.get("security_constraints") or []
        ),
        "acceptance_criteria": list(normalized.get("acceptance_criteria") or []),
        "priority": normalized.get("priority") or "",
        "verification_method": normalized.get("verification_method") or "",
        "parent_requirement_ids": list(
            normalized.get("parent_requirement_ids") or []
        ),
        "source_chunk_ids": list(normalized.get("source_chunk_ids") or []),
        "source_documents": list(
            normalized.get("source_documents")
            or [normalized.get("source_document") or source_document]
        ),
        "source_document": normalized.get("source_document") or source_document,
        "source_evidence": list(normalized.get("source_evidence") or []),
        "missing_information": list(normalized.get("missing_information") or []),
        "need_human_confirm": bool(normalized.get("need_human_confirm")),
    })
    verdict = infer_test_type(
        normalized, str(normalized.get("recommended_test_type") or "")
    )
    if not normalized.get("recommended_test_type"):
        normalized.update(verdict)
    return normalized


def _template_rows(path: Path) -> List[Dict[str, Any]]:
    """Adapt the existing fixed-template parser to the rich preview schema."""
    rows: List[Dict[str, Any]] = []
    for item in parse_requirement_file(path):
        text = str(item.requirement_text or "").strip()
        if not text:
            continue
        rows.append(_normalize_requirement_row({
            "requirement_id": str(item.requirement_id or "").strip(),
            "title": text.splitlines()[0][:120],
            "description": text,
            "test_object": str(item.test_object or ""),
            "source_document": path.name,
            "source_evidence": [{"text": text, "parser": "fixed_template"}],
            "need_human_confirm": True,
        }, source_document=path.name))
    return rows


def _chunks_for_document(
    manager: Any,
    project_id: str,
    document_id: str,
) -> List[Dict[str, Any]]:
    return [
        row
        for row in manager.list_chunks(project_id, 5000)
        if str(row.get("document_id") or "") == document_id
    ]


def _make_rows_unique(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Avoid cross-document primary-key collisions without losing original IDs."""
    result: List[Dict[str, Any]] = []
    used_ids: set[str] = set()
    seen_text: set[str] = set()
    for index, original in enumerate(rows, 1):
        row = dict(original)
        description_key = re.sub(r"\s+", "", str(row.get("description") or ""))
        if description_key and description_key in seen_text:
            continue
        if description_key:
            seen_text.add(description_key)
        base_id = str(row.get("requirement_id") or "").strip()
        if not base_id:
            base_id = f"REQ-{index:03d}"
        requirement_id = base_id
        suffix = 2
        while requirement_id in used_ids:
            requirement_id = f"{base_id}-{suffix}"
            suffix += 1
        row["requirement_id"] = requirement_id
        used_ids.add(requirement_id)
        result.append(row)
    return result


def extract_structured_requirements_with_report(
    manager: Any, project_id: str
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    all_blocks: List[DocumentBlock] = []
    warnings: List[str] = []
    document_reports: List[Dict[str, Any]] = []
    fallback_used = False
    for doc in manager.list_documents(project_id):
        path = Path(str(doc.get("file_path") or ""))
        suffix = path.suffix.lower()
        document_id = str(doc.get("document_id") or "")
        document_rows: List[Dict[str, Any]] = []
        document_blocks: List[DocumentBlock] = []
        strategy = "none"
        document_warnings: List[str] = []

        if suffix == ".docx" and path.is_file():
            try:
                document_blocks = parse_docx_blocks(path, document_id)
                document_rows = extract_requirements_from_blocks(document_blocks)
                if document_rows:
                    strategy = "structure_first"
            except Exception as exc:
                document_warnings.append(
                    f"结构化解析失败：{type(exc).__name__}: {exc}"
                )
        elif suffix == ".doc" and path.is_file():
            try:
                converted = _convert_doc_to_temp_docx(path)
                document_blocks = parse_docx_blocks(converted, document_id)
                for block in document_blocks:
                    block.source_document = path.name
                document_rows = extract_requirements_from_blocks(document_blocks)
                if document_rows:
                    strategy = "structure_first_after_doc_conversion"
                document_warnings.append("已临时转换为DOCX解析，原文件未覆盖")
            except Exception as exc:
                document_warnings.append(
                    f"DOC转换或结构化解析失败：{type(exc).__name__}: {exc}"
                )
        elif suffix in {".docx", ".doc"} and not path.is_file():
            document_warnings.append("原始上传文件不存在，尝试使用已入库文本")

        if suffix in {".docx", ".doc"} and path.is_file() and not document_rows:
            try:
                document_rows = _template_rows(path)
                if document_rows:
                    strategy = "fixed_template"
            except Exception as exc:
                document_warnings.append(
                    f"固定模板解析失败：{type(exc).__name__}: {exc}"
                )

        if not document_rows:
            chunks = _chunks_for_document(manager, project_id, document_id)
            fallback = extract_requirements_from_chunks(chunks)
            document_rows = [
                _normalize_requirement_row(row, source_document=path.name)
                for row in fallback
            ]
            if document_rows:
                strategy = "chunk_keyword_fallback"
                fallback_used = True
                document_warnings.append("已使用该文件的普通文本兜底抽取")
            else:
                strategy = "no_requirements_found"
                document_warnings.append("未从该文件识别到需求，请人工检查")

        all_blocks.extend(document_blocks)
        rows.extend(document_rows)
        warnings.extend(f"{path.name}：{item}" for item in document_warnings)
        document_reports.append({
            "document_id": document_id,
            "filename": str(doc.get("filename") or path.name),
            "file_type": suffix.lstrip("."),
            "strategy": strategy,
            "block_count": len(document_blocks),
            "requirement_count": len(document_rows),
            "warnings": document_warnings,
        })

    rows = _make_rows_unique(rows)
    report = build_extraction_quality_report(
        all_blocks,
        rows,
        filename="",
        warnings=warnings,
        fallback_used=fallback_used,
    )
    report["document_reports"] = document_reports
    report["extraction_strategies"] = {
        item["filename"]: item["strategy"] for item in document_reports
    }
    return {"requirements": rows, "report": report}


def extract_structured_requirements(manager: Any, project_id: str) -> List[Dict[str, Any]]:
    return list(extract_structured_requirements_with_report(manager, project_id)["requirements"])
