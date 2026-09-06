"""Ordered DOCX parser for formal 3.2/3.3 CSCI documents."""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import re
from typing import Any, Iterator
from docx import Document
from docx.document import Document as _Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from domain.schemas.traceability import RequirementNode

FOUR_SECTIONS=("功能描述","输入","处理","输出")
GUIDANCE=("本条应","本章应","本部分应","这部分应","例如","示例","填写说明")

def _id(prefix:str,*parts:str)->str:
    return f"{prefix}-{sha256('|'.join(parts).encode('utf-8')).hexdigest()[:16].upper()}"

def _blocks(document:_Document)->Iterator[Paragraph|Table]:
    for child in document.element.body.iterchildren():
        if isinstance(child,CT_P): yield Paragraph(child,document)
        elif isinstance(child,CT_Tbl): yield Table(child,document)

def _heading(paragraph:Paragraph)->tuple[int,str,str]|None:
    text=paragraph.text.strip(); style=str(paragraph.style.name or "")
    number_match=re.match(r"^(\d+(?:\.\d+)*)\s+(.+)$",text)
    style_match=re.search(r"(?:Heading|标题)\s*([1-9])",style,re.I)
    if not number_match and not style_match: return None
    number=number_match.group(1) if number_match else ""
    title=number_match.group(2).strip() if number_match else text
    level=int(style_match.group(1)) if style_match else len(number.split("."))
    return level,number,title

def _name_identifier(title:str)->tuple[str,str]:
    text=title.strip(); match=re.match(r"^\[?([A-Z][A-Z0-9_]{1,})\]?\s*(.+)$",text)
    if not match: return text,""
    return match.group(2).strip(),match.group(1)

def parse_formal_csci_docx(path:Path)->dict[str,Any]:
    document=Document(path); nodes=[]; overview=[]; detail=[]; stack=[]; active_part=""; active_section=""; block_no=0; boundaries={}
    current:RequirementNode|None=None
    for block in _blocks(document):
        block_no+=1
        if isinstance(block,Paragraph):
            text=block.text.strip(); heading=_heading(block)
            if heading:
                level,number,title=heading
                semantic=re.sub(r"[：:（）()\s]","",title).upper()
                if "CSCI能力需求" in semantic or (number=="3.2" and "总体需求" in semantic):
                    active_part="overview"; boundaries["overview"]={"number":number,"title":title,"block_index":block_no}; active_section=""; stack=[]; current=None; continue
                if semantic in {"CSCI能力","CSCI能力详细需求","CSCI具体能力"} or (number=="3.3" and "详细功能需求" in semantic):
                    active_part="detail"; boundaries["detail"]={"number":number,"title":title,"block_index":block_no}; active_section=""; stack=[]; current=None; continue
                if active_part and number and boundaries.get(active_part,{}).get("number"):
                    boundary_number=boundaries[active_part]["number"]
                    if len(number.split("."))<=len(boundary_number.split(".")) and not number.startswith(boundary_number+"."):
                        active_part=""; active_section=""; stack=[]; current=None
                        continue
                if not active_part: continue
                if active_part=="detail" and title in FOUR_SECTIONS:
                    active_section=title; continue
                active_section=""; name,identifier=_name_identifier(title)
                while stack and stack[-1].level>=level: stack.pop()
                ancestors=list(stack); path_names=[x.name for x in ancestors]+[name]
                node=RequirementNode(node_id=_id("NODE",path.name,number,identifier,name),name=name,identifier=identifier or _id("AUTO",path.name,number,name),identifier_generated=not bool(identifier),level=level,parent_id=ancestors[-1].node_id if ancestors else "",section_number=number,hierarchy_path=path_names,ancestor_node_ids=[x.node_id for x in ancestors],ancestor_identifiers=[x.identifier for x in ancestors if x.identifier],node_type="subsystem_overview" if active_part=="overview" else ("group" if name=="功能需求" else "function"),source_document=path.name,source_block_id=f"block-{block_no}",source_position={"block_index":block_no,"kind":"heading"},need_human_confirm=not bool(identifier))
                nodes.append(node); (overview if active_part=="overview" else detail).append(node); stack.append(node); current=node; continue
            if current and text and not text.startswith(GUIDANCE):
                key=active_section or ("总体需求概述" if active_part=="overview" else "正文")
                current.sections[key]=(current.sections.get(key,"")+"\n"+text).strip()
                current.section_evidence.setdefault(key,[]).append({"kind":"paragraph","block_index":block_no,"text":text})
                if block._p.xpath(".//a:blip"):
                    current.section_evidence.setdefault(active_section or "图片",[]).append({"kind":"image","block_index":block_no,"caption":text,"nearby_heading":current.name,"need_human_confirm":True})
        elif current:
            rows=[]
            for row_index,row in enumerate(block.rows,1):
                cells=[cell.text.strip() for cell in row.cells]; rows.append(cells)
            if rows:
                key=active_section or ("总体需求概述" if active_part=="overview" else "正文"); header=rows[0]
                rendered="\n".join(" | ".join(row) for row in rows); current.sections[key]=(current.sections.get(key,"")+"\n"+rendered).strip()
                for row_index,row in enumerate(rows,1): current.section_evidence.setdefault(key,[]).append({"kind":"table_row","block_index":block_no,"table_index":sum(1 for x in current.section_evidence.get(key,[]) if x.get('kind')=='table_row' and x.get('row_index')==1)+1,"row_index":row_index,"headers":header,"cells":row})
    overview_by_id={x.identifier:x for x in overview if x.identifier}
    for node in detail:
        subsystem=next((identifier for identifier in node.ancestor_identifiers+[node.identifier] if identifier in overview_by_id),"")
        if subsystem: node.overview_node_id=overview_by_id[subsystem].node_id
    complete=[x for x in detail if all(x.sections.get(k,"").strip() for k in FOUR_SECTIONS)]
    complete_ids={x.node_id for x in complete}
    for node in complete: node.testable=not any(node.node_id in child.ancestor_node_ids for child in complete if child.node_id!=node.node_id)
    confidence=1.0 if overview and detail and any(x.testable for x in complete) else (0.5 if overview or detail else 0.0)
    return {"nodes":nodes,"overview_nodes":overview,"detail_nodes":detail,"testable_nodes":[x for x in complete if x.testable],"boundaries":boundaries,"structure_confidence":confidence,"extraction_method":"csci_structured"}
