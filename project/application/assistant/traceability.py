"""Deterministic requirement extraction and traceability matrix validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict


HEADERS = ["序号","需求标识","需求名称","需求描述","来源章节/位置","关联测试类型","关联测试用例","覆盖状态","备注"]
ID_RE = re.compile(r"\b([A-Z][A-Z0-9_-]{1,30}[-_][A-Z0-9_-]*\d+[A-Z0-9_-]*)\b", re.I)
CN_ID_RE = re.compile(r"(?:需求(?:编号|标识)?[：:]?\s*)([A-Za-z0-9_-]+)", re.I)


@dataclass(frozen=True)
class TraceabilityItem:
    sequence: int; requirement_id: str; name: str; description: str; source: str
    test_type: str = "待人工确认"; test_case: str = ""; coverage_status: str = "未覆盖"; note: str = ""
    def row(self)->list[str]: return [str(self.sequence),self.requirement_id,self.name,self.description,self.source,self.test_type,self.test_case,self.coverage_status,self.note]
    def to_dict(self)->dict: return asdict(self)


def extract_traceability_items(parsed: dict) -> list[TraceabilityItem]:
    metadata=parsed.get("metadata") or {}; blocks=metadata.get("blocks") or []; items=[]; seen=set(); temporary=1
    for block in blocks:
        candidates=[]
        if block.get("kind")=="paragraph": candidates=[(block.get("text", ""), block.get("source", ""))]
        elif block.get("kind")=="table":
            candidates=[(" | ".join(str(value) for value in row if value not in (None,"")),f"{block.get('source','表格')}第{index}行") for index,row in enumerate(block.get("rows") or [],start=1)]
        for text,source in candidates:
            text=text.strip()
            if not text or len(text)<4: continue
            match=CN_ID_RE.search(text) or ID_RE.search(text); requirement_id=match.group(1) if match else ""
            looks_like_requirement=bool(requirement_id or re.search(r"(?:应|必须|需要|shall|requirement)",text,re.I))
            if not looks_like_requirement: continue
            if not requirement_id: requirement_id=f"TEMP-{temporary:04d}"; temporary+=1; note="程序生成，待人工确认"
            else: note=""
            key=(requirement_id,text)
            if key in seen: continue
            seen.add(key); cleaned=re.sub(r"^(?:需求(?:编号|标识)?[：:]?\s*)?[A-Za-z0-9_-]+\s*[：:\-]?\s*","",text).strip() or text
            name=cleaned[:60]; items.append(TraceabilityItem(len(items)+1,requirement_id,name,cleaned,source,coverage_status="待人工确认" if note else "未覆盖",note=note))
    return items


def validate_traceability_matrix(items:list[TraceabilityItem])->None:
    if not items: raise ValueError("未提取到可生成追踪矩阵的需求，禁止输出空矩阵")
    for index,item in enumerate(items,start=1):
        if item.sequence!=index or not item.requirement_id or not item.description or not item.source: raise ValueError(f"第{index}行追踪数据不完整")


def matrix_docx_content(title:str,items:list[TraceabilityItem])->dict:
    validate_traceability_matrix(items)
    return {"title":title,"paragraphs":[f"共提取 {len(items)} 条需求。临时标识和关联关系均需人工确认。"],"tables":[[HEADERS]+[item.row() for item in items]],"header":"小测需求追踪矩阵","footer":"本矩阵由本地确定性工具生成"}
