"""General LLM extraction into the same RequirementNode contract as CSCI parsing."""
from __future__ import annotations
import json
from hashlib import sha256
from pathlib import Path
from typing import Any,Callable
import requests
from config.settings import Settings
from domain.schemas.traceability import RequirementNode
from workflows.learning.structured_requirement_extractor import parse_docx_blocks

SCHEMA={"type":"object","required":["requirements"],"properties":{"requirements":{"type":"array","items":{"type":"object","required":["name","identifier","hierarchy_path","source_block_ids","functional_description","inputs","processing","outputs","constraints","normal_behaviors","abnormal_behaviors","boundary_conditions","permissions","prompts","need_human_confirm","confidence"],"properties":{"name":{"type":"string"},"identifier":{"type":"string"},"hierarchy_path":{"type":"array","items":{"type":"string"}},"parent_id":{"type":"string"},"source_block_ids":{"type":"array","items":{"type":"string"}},"functional_description":{"type":"string"},"inputs":{"type":"array","items":{"type":"string"}},"processing":{"type":"array","items":{"type":"string"}},"outputs":{"type":"array","items":{"type":"string"}},"constraints":{"type":"array","items":{"type":"string"}},"normal_behaviors":{"type":"array","items":{"type":"string"}},"abnormal_behaviors":{"type":"array","items":{"type":"string"}},"boundary_conditions":{"type":"array","items":{"type":"string"}},"permissions":{"type":"array","items":{"type":"string"}},"prompts":{"type":"array","items":{"type":"string"}},"need_human_confirm":{"type":"boolean"},"confidence":{"type":"number"}}}}}}

def _chat(settings:Settings,payload:dict[str,Any])->dict[str,Any]:
    last=None
    for _ in range(settings.ollama_max_retries+1):
        try:
            r=requests.post(settings.ollama_base_url.rstrip('/')+'/api/chat',json={"model":settings.text_model,"stream":False,"think":False,"format":SCHEMA,"messages":[{"role":"user","content":"从有序文档块提取可独立测试的最低功能。不得编造；保留层级、约束、正常/异常/边界、权限和提示；source_block_ids只能取输入ID。只输出JSON。\n"+json.dumps(payload,ensure_ascii=False)}],"options":{"temperature":0,"num_predict":3000,"num_ctx":settings.ollama_num_ctx}},timeout=settings.ollama_timeout)
            r.raise_for_status(); data=json.loads(r.json()['message']['content'])
            if not data.get('requirements'): raise ValueError('通用AI未返回需求')
            return data
        except Exception as exc: last=exc
    raise RuntimeError(f"通用AI需求抽取失败: {type(last).__name__}: {last}")

def extract_general_requirements(path:Path,settings:Settings,progress:Callable[[dict],None]|None=None)->dict[str,Any]:
    blocks=parse_docx_blocks(path); compact=[{"block_id":b.block_id,"type":b.block_type,"heading_level":b.heading_level,"section_path":b.section_path,"text":b.text[:2000]} for b in blocks if b.text]
    groups=[]; current=[]; size=0
    for block in compact:
        length=len(block['text'])
        if current and size+length>12000: groups.append(current); current=[]; size=0
        current.append(block); size+=length
    if current: groups.append(current)
    extracted=[]
    for index,group in enumerate(groups,1):
        if progress: progress({"stage":"通用AI分块抽取","index":index,"total":len(groups),"object":path.name})
        extracted.extend(_chat(settings,{"source_document":path.name,"ordered_blocks":group})['requirements'])
    valid_ids={b['block_id'] for b in compact}; nodes=[]; warnings=[]
    for index,row in enumerate(extracted,1):
        description=str(row.get('functional_description') or '').strip(); sources=[x for x in row.get('source_block_ids',[]) if x in valid_ids]
        if not description or not sources: warnings.append(f"第{index}项缺少功能描述或有效来源块，已丢弃"); continue
        identifier=str(row.get('identifier') or '').strip() or 'AI-'+sha256((path.name+description).encode()).hexdigest()[:16].upper()
        hierarchy=[str(x) for x in row.get('hierarchy_path',[]) if str(x).strip()] or [str(row.get('name') or identifier)]
        sections={"功能描述":description,"输入":"\n".join(row.get('inputs') or []),"处理":"\n".join(row.get('processing') or []),"输出":"\n".join(row.get('outputs') or []),"约束":"\n".join(row.get('constraints') or []),"正常行为":"\n".join(row.get('normal_behaviors') or []),"异常行为":"\n".join(row.get('abnormal_behaviors') or []),"边界条件":"\n".join(row.get('boundary_conditions') or []),"权限":"\n".join(row.get('permissions') or []),"提示":"\n".join(row.get('prompts') or [])}
        nodes.append(RequirementNode(node_id='NODE-'+sha256((path.name+identifier).encode()).hexdigest()[:16].upper(),name=str(row.get('name') or hierarchy[-1]),identifier=identifier,identifier_generated=not bool(row.get('identifier')),level=len(hierarchy),hierarchy_path=hierarchy,node_type='function',source_document=path.name,source_block_id=sources[0],source_position={"block_ids":sources},sections=sections,section_evidence={"功能描述":[{"block_id":x} for x in sources]},testable=True,need_human_confirm=bool(row.get('need_human_confirm')) or float(row.get('confidence',0))<.8))
    return {"nodes":nodes,"testable_nodes":nodes,"overview_nodes":[],"warnings":warnings,"extraction_method":"llm_general","structure_confidence":sum(float(x.get('confidence',0)) for x in extracted)/max(1,len(extracted)),"block_count":len(blocks)}
