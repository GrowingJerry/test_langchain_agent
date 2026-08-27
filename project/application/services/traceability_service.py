"""Deterministic CSCI, HTML, matching, coverage and case-version services."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from typing import Any, Iterable
from pathlib import Path

from domain.schemas.traceability import (
    CoveragePlanItem, HtmlElement, RequirementIndicator, RequirementNode,
)

SECTION_NAMES = ("功能描述", "输入", "处理", "输出")
GUIDANCE_PREFIXES = ("本条应", "本章应", "本部分应", "这部分应", "例如", "示例", "填写说明")


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(str(p).strip().casefold() for p in parts)
    return f"{prefix}-{sha256(raw.encode('utf-8')).hexdigest()[:16].upper()}"


def _clean_identifier(text: str) -> tuple[str, str]:
    match = re.search(r"(?:标识符|标识)\s*[_：:]?\s*([A-Za-z][A-Za-z0-9_-]+)", text)
    name = re.sub(r"[/（(]?\s*(?:标识符|标识)\s*[_：:]?\s*[A-Za-z][A-Za-z0-9_-]+\s*[）)]?", "", text).strip(" /-")
    return name, match.group(1) if match else ""


def _heading(line: str) -> tuple[int, str, str] | None:
    line = line.strip()
    if not line or line in SECTION_NAMES:
        return None
    hash_match = re.match(r"^(#{1,6})\s*(.+)$", line)
    if hash_match:
        return len(hash_match.group(1)), "", hash_match.group(2).strip()
    match = re.match(r"^(?:(\d+(?:\.\d+)*)[.、]?\s+)?(?:#{1,6}\s*)?(.+)$", line)
    if not match:
        return None
    number, title = match.groups()
    if number:
        return len(number.split(".")), number, title.strip()
    # Plain headings in normalized DOCX text use indentation lost by extraction;
    # accept short non-sentence lines, with structural section names excluded above.
    if len(title) <= 60 and not re.search(r"[。；;]$", title):
        return 2, "", title
    return None


def parse_csci_structure(text: str, source_document: str = "") -> list[RequirementNode]:
    """Parse arbitrary-depth units inside the CSCI capability-requirements boundary."""
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    boundary = next((i for i, line in enumerate(lines) if "CSCI能力需求" in line), -1)
    if boundary < 0:
        return []
    boundary_heading = _heading(lines[boundary])
    boundary_level = boundary_heading[0] if boundary_heading else 1
    nodes: list[RequirementNode] = []
    stack: list[RequirementNode] = []
    current_section = ""
    section_lines: list[str] = []

    def flush() -> None:
        nonlocal section_lines
        if stack and current_section:
            useful = [x for x in section_lines if not x.startswith(GUIDANCE_PREFIXES)]
            stack[-1].sections[current_section] = "\n".join(useful).strip()
        section_lines = []

    for index, line in enumerate(lines[boundary + 1 :], boundary + 1):
        candidate = _heading(line)
        candidate_title = candidate[2] if candidate else line
        if candidate_title in SECTION_NAMES:
            flush()
            current_section = candidate_title
            continue
        # A real heading following section content starts the next unit/boundary.
        if candidate and current_section and (candidate[1] or line.startswith("#")):
            flush(); current_section = ""
        if candidate and not current_section:
            level, number, title = candidate
            if level <= boundary_level and nodes:
                break
            name, identifier = _clean_identifier(title)
            if name in {"外部接口", "内部接口", "通用质量特性", "合格性规定", "需求可追踪性", "模板填写说明"} and level <= boundary_level:
                break
            while stack and stack[-1].level >= level:
                stack.pop()
            path = [n.name for n in stack] + [name]
            generated = not bool(identifier)
            identifier = identifier or _stable_id("AUTO", source_document, *path)
            node = RequirementNode(
                node_id=_stable_id("NODE", source_document, *path), name=name,
                identifier=identifier, identifier_generated=generated, level=level,
                parent_id=stack[-1].node_id if stack else "", section_number=number,
                hierarchy_path=path, source_document=source_document,
                source_block_id=f"block-{index}", need_human_confirm=generated,
            )
            nodes.append(node); stack.append(node)
            continue
        if current_section:
            section_lines.append(line)
    flush()
    return [node for node in nodes if all(node.sections.get(key, "").strip() for key in SECTION_NAMES)]


def parse_csci_docx(path: Path) -> list[RequirementNode]:
    """Preserve Word Heading styles and table text while parsing CSCI leaves."""
    from docx import Document

    document = Document(path)
    lines: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = str(paragraph.style.name or "")
        level_match = re.search(r"(?:Heading|标题)\s*([1-9])", style, re.I)
        lines.append(f"{'#' * int(level_match.group(1))} {text}" if level_match else text)
    # Tables are evidence for the active Input/Processing/Output section. Keeping
    # cells as readable rows lets the same deterministic atomizer consume them.
    for table_index, table in enumerate(document.tables):
        for row_index, row in enumerate(table.rows):
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                lines.append(f"[表{table_index + 1}行{row_index + 1}] " + " | ".join(cells))
    return parse_csci_structure("\n".join(lines), path.name)


def atomic_indicators(node: RequirementNode) -> list[RequirementIndicator]:
    """Split a leaf's functional description into independently verifiable clauses."""
    source = node.sections.get("功能描述", "")
    fragments: list[str] = []
    for sentence in re.split(r"[。；;\n]+", source):
        sentence = sentence.strip(" ，,")
        if not sentence or sentence.startswith(GUIDANCE_PREFIXES):
            continue
        fragments.extend(x.strip() for x in re.split(r"[，,](?=(?:并|且)?(?:支持|显示|展示|保存|提示|校验|不得|不能|必须|应|用户))", sentence) if x.strip())
    capability_id = node.hierarchy_path[0] if node.hierarchy_path else node.identifier
    result = []
    for fragment in fragments:
        kind = "其他"
        for token, mapped in (("显示", "展示"), ("查看", "展示"), ("输入", "输入"), ("点击", "点击"),
                              ("选择", "选择"), ("默认", "默认状态"), ("格式", "校验"), ("不能为空", "校验"),
                              ("保存", "保存"), ("删除", "删除"), ("权限", "权限"), ("提示", "提示"), ("输出", "输出")):
            if token in fragment:
                kind = mapped; break
        online = any(word in fragment for word in ("数据库", "服务端", "接口", "持久化", "消息推送"))
        result.append(RequirementIndicator(
            indicator_id=_stable_id("IND", node.node_id, fragment), capability_id=capability_id,
            function_id=node.identifier, indicator_text=fragment, indicator_type=kind,
            source_block_id=node.source_block_id, source_text=source,
            input_constraints=[node.sections["输入"]] if node.sections.get("输入") else [],
            processing_rules=[node.sections["处理"]] if node.sections.get("处理") else [],
            expected_behavior=[node.sections["输出"]] if node.sections.get("输出") else [],
            verification_scope="online_required" if online else "offline",
            need_human_confirm=online,
        ))
    return result


@dataclass
class _RawElement:
    tag: str; attrs: dict[str, str]; text: str = ""; path: str = ""; form_id: str = ""
    options: list[str] = field(default_factory=list)


class _HTMLCollector(HTMLParser):
    TARGETS = {"input", "button", "select", "textarea", "a", "option"}
    VOID = {"area","base","br","col","embed","hr","img","input","link","meta","param","source","track","wbr"}
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True); self.stack=[]; self.elements=[]; self.title=""; self.labels={}; self._label_for=""
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs); path="/".join([*self.stack,tag])
        if tag not in self.VOID: self.stack.append(tag)
        if tag == "label": self._label_for = attrs.get("for", "")
        if tag in self.TARGETS:
            form_id = next((e.attrs.get("id", "") for e in reversed(self.elements) if e.tag == "form"), "")
            self.elements.append(_RawElement(tag, attrs, path=path, form_id=form_id))
        elif tag == "form": self.elements.append(_RawElement(tag, attrs, path=path))
    def handle_endtag(self, tag):
        if tag == "label": self._label_for = ""
        if self.stack:
            try:
                index=len(self.stack)-1-self.stack[::-1].index(tag)
                del self.stack[index:]
            except ValueError: pass
    def handle_data(self, data):
        value = data.strip()
        if not value: return
        if self.stack and self.stack[-1] == "title": self.title += value
        if self._label_for: self.labels[self._label_for] = (self.labels.get(self._label_for, "") + value).strip()
        if self.elements and self.stack and self.elements[-1].tag == self.stack[-1]: self.elements[-1].text += value


def iter_offline_html_elements(content: str | bytes, page_path: str = "index.html"):
    """Parse once and yield elements one by one to keep large-page memory bounded."""
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    parser = _HTMLCollector(); parser.feed(text)
    page_id = _stable_id("PAGE", page_path)
    def generate():
        for index, raw in enumerate(e for e in parser.elements if e.tag != "form"):
            allowed={"id","name","type","role","aria-label","placeholder","value","required","readonly","disabled","min","max","minlength","maxlength","pattern","checked","selected","href","action","target"}
            attrs={k:(str(v)[:500] if v is not None else "") for k,v in raw.attrs.items() if k.lower() in allowed or k.lower().startswith("on")}
            if str(attrs.get("value","")).lower().startswith("data:"): attrs["value"]="[inline resource omitted]"
            stable_name=attrs.get("id") or attrs.get("name") or attrs.get("aria-label") or parser.labels.get(attrs.get("id", ""), "") or attrs.get("placeholder") or raw.text.strip()
            element_id=_stable_id("EL", page_id, raw.tag, stable_name, raw.path)
            label=parser.labels.get(attrs.get("id", ""), ""); locators=[]
            if attrs.get("role") and (attrs.get("aria-label") or label): locators.append(f"role={attrs['role']} name={attrs.get('aria-label') or label}")
            if label: locators.append(f"label={label}")
            if attrs.get("id"): locators.append(f"#{attrs['id']}")
            locators.append(raw.path)
            hidden="hidden" in attrs or attrs.get("type")=="hidden" or "display:none" in attrs.get("style","").replace(" ","")
            landmark = next((tag for tag in reversed(raw.path.split("/")) if tag in {"header","nav","main","aside","section","form","dialog","fieldset","table"}), "")
            region_names={"header":"页眉区","nav":"导航区","main":"主内容区","aside":"侧栏区","section":"内容分区","form":"表单区","dialog":"对话框","fieldset":"字段组","table":"表格区"}
            semantic_position=region_names.get(landmark, "方位待确认")
            yield HtmlElement(element_id=element_id,page_id=page_id,tag=raw.tag,element_type=attrs.get("type",raw.tag),text=raw.text.strip(),attributes=attrs,label=label,visible=not hidden,enabled="disabled" not in attrs,default_value=attrs.get("value",""),form_id=raw.form_id,local_events=sorted(k for k in attrs if k.lower().startswith("on")),locator_candidates=locators,semantic_position=semantic_position,dom_path=raw.path,evidence_source="source_static",stability="high" if stable_name else "low")
    return {"page_id":page_id,"title":parser.title or page_path,"path":page_path},generate()


def parse_offline_html(content: str | bytes, page_path: str = "index.html") -> tuple[dict[str, str], list[HtmlElement]]:
    page,elements=iter_offline_html_elements(content,page_path)
    return page,list(elements)


def match_indicator_elements(indicator: RequirementIndicator, elements: Iterable[HtmlElement]) -> dict[str, Any]:
    tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_-]+", indicator.indicator_text.casefold()))
    candidates=[]
    for element in elements:
        haystack=" ".join([element.text, element.label, *map(str, element.attributes.values())]).casefold()
        hits=sorted(t for t in tokens if t in haystack)
        if hits:
            score=min(0.99, 0.45 + len(hits)/max(1, len(tokens))*0.5)
            candidates.append({"element_id": element.element_id, "confidence": round(score, 3), "reason": f"匹配文本：{', '.join(hits)}"})
    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    return {"indicator_id": indicator.indicator_id, "confidence": candidates[0]["confidence"] if candidates else 0.0,
            "reason": candidates[0]["reason"] if candidates else "HTML中未找到对应元素", "candidates": candidates,
            "confirmed_element_id": candidates[0]["element_id"] if candidates and candidates[0]["confidence"] >= .8 else "",
            "need_human_confirm": not candidates or candidates[0]["confidence"] < .8}


def build_coverage_plan(indicators: Iterable[RequirementIndicator], links: Iterable[dict[str, Any]] = ()) -> list[CoveragePlanItem]:
    by_indicator={x.get("indicator_id"): x for x in links}
    plans=[]
    for item in indicators:
        link=by_indicator.get(item.indicator_id, {}); kind=item.indicator_type
        category="异常" if kind in {"校验", "异常", "权限"} else "正常"
        if any(x in item.indicator_text for x in ("最大", "最小", "边界", "长度")): category="边界"
        element_ids=[link["confirmed_element_id"]] if link.get("confirmed_element_id") else []
        plans.append(CoveragePlanItem(function_id=item.function_id, indicator_id=item.indicator_id,
            source_location=f"{item.source_section}/{item.source_block_id}", html_element_ids=element_ids,
            test_types=["功能测试"], category=category, offline_verifiable=item.verification_scope == "offline",
            need_human_confirm=item.need_human_confirm or link.get("need_human_confirm", False)))
    return plans


def coverage_matrix(indicators: Iterable[RequirementIndicator], cases: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows=[]; cases=list(cases)
    for indicator in indicators:
        bound=[case for case in cases if indicator.indicator_id in (case.get("indicator_ids") or [])]
        online=indicator.verification_scope == "online_required"
        status="待联机验证" if bound and online else "已覆盖" if bound else "未覆盖"
        rows.append({"capability_id": indicator.capability_id, "function_id": indicator.function_id,
            "indicator_id": indicator.indicator_id, "indicator_text": indicator.indicator_text,
            "case_ids": [c.get("case_id", "") for c in bound], "coverage_status": status,
            "offline_verifiable": not online, "need_human_confirm": indicator.need_human_confirm,
            "uncovered_reason": "尚无绑定该原子需求的用例" if not bound else ""})
    return rows


def validate_step_alignment(case: dict[str, Any]) -> None:
    steps=case.get("test_steps") or case.get("steps") or []
    expected=case.get("expected_result") or case.get("expected_results") or case.get("expected") or []
    if len(steps) != len(expected):
        raise ValueError("测试步骤与预期结果必须严格一一对应")


def enforce_online_confirmation(case: dict[str, Any], online_indicator_ids: Iterable[str] = ()) -> dict[str, Any]:
    """Prevent model output from treating server/database behavior as offline proof."""
    bound=set(case.get("indicator_ids") or [])
    text=json.dumps(case,ensure_ascii=False)
    requires_online=bool(bound & set(online_indicator_ids)) or any(x in text for x in ("数据库","服务端","持久化","消息推送"))
    if requires_online:
        case["need_human_confirm"]=True; case["expected_source"]="requirement_pending_online"
        expected=case.get("expected_result") or case.get("expected_results") or case.get("expected") or []
        if expected and "联机" not in json.dumps(expected,ensure_ascii=False): expected[-1]=str(expected[-1]).rstrip("。")+"；数据库或服务端结果待联机验证。"
    return case
