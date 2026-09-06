"""Lossless model atomization followed by an independent coverage audit."""
from __future__ import annotations
import json,re
from hashlib import sha256
from typing import Any
import requests
from config.settings import Settings
from domain.schemas.traceability import RequirementIndicator, RequirementNode
from infrastructure.llm.ollama_errors import NonRetryableSchemaError, raise_for_ollama_status, response_error_text
import logging
logger=logging.getLogger("test_agent.requirement_atomization")

def deterministic_candidates(text:str)->list[str]:
    result=[]
    for sentence in re.split(r"[。；;\n]+",text):
        sentence=sentence.strip(" ，,")
        if not sentence: continue
        pieces=re.split(r"[，,](?=(?:支持|配置|显示|查看|新增|修改|删除|保存|发布|校验|提示|按))",sentence)
        result.extend(x.strip() for x in pieces if x.strip())
    return list(dict.fromkeys(result))

def _chat(settings:Settings,model:str,prompt:str,schema:dict[str,Any])->dict[str,Any]:
    error=""
    for _ in range(settings.ollama_max_retries+1):
        response=requests.post(settings.ollama_base_url.rstrip("/")+"/api/chat",json={"model":model,"stream":False,"think":False,"format":schema,"messages":[{"role":"user","content":prompt+("\n上次错误："+error if error else "")}],"options":{"temperature":0,"num_ctx":settings.ollama_num_ctx,"num_predict":settings.ollama_structured_num_predict}},timeout=settings.ollama_timeout)
        try:
            if not response.ok: logger.error("Atomization Ollama HTTP %s response=%s",response.status_code,response_error_text(response))
            raise_for_ollama_status(response); return json.loads(response.json()["message"]["content"])
        except NonRetryableSchemaError: raise
        except Exception as exc: error=f"{type(exc).__name__}: {exc}"
    raise RuntimeError(error)

def atomize_and_audit(node:RequirementNode,overview:str,settings:Settings)->dict[str,Any]:
    description=node.sections.get("功能描述",""); candidates=deterministic_candidates(description)
    atom_schema={"type":"object","required":["atoms"],"properties":{"atoms":{"type":"array","minItems":1,"items":{"type":"object","required":["text","type","evidence_spans"],"properties":{"text":{"type":"string","minLength":1},"type":{"type":"string"},"evidence_spans":{"type":"array","minItems":1,"items":{"type":"string"}}}}}}}
    prompt="你是需求原子化器。不得总结、省略、编造或改变含义；一个原子项只含一个主要可验证行为；可独立测试的枚举对象必须拆开。只输出JSON。\n"+json.dumps({"subsystem_overview":overview,"hierarchy_path":node.hierarchy_path,"functional_description":description,"input":node.sections.get("输入",""),"processing":node.sections.get("处理",""),"output":node.sections.get("输出",""),"deterministic_candidates":candidates},ensure_ascii=False)
    atom_data=_chat(settings,settings.requirement_atomizer_model,prompt,atom_schema)
    atoms=[]
    for item in atom_data["atoms"]:
        spans=[span for span in item["evidence_spans"] if span and span in description]
        if not spans: spans=[candidate for candidate in candidates if candidate in item["text"] or item["text"] in candidate]
        atoms.append(RequirementIndicator(indicator_id="ATOM-"+sha256((node.node_id+item["text"]).encode()).hexdigest()[:16].upper(),capability_id=node.ancestor_identifiers[0] if node.ancestor_identifiers else node.identifier,function_id=node.identifier,indicator_text=item["text"],indicator_type=item["type"] if item["type"] in RequirementIndicator.model_fields["indicator_type"].annotation.__args__ else "其他",source_text=description,source_block_id=node.source_block_id,input_constraints=[node.sections.get("输入","")],processing_rules=[node.sections.get("处理","")],expected_behavior=[node.sections.get("输出","")],evidence_spans=spans,mandatory_coverage=True,need_human_confirm=not bool(spans)))
    audit_schema={"type":"object","required":["coverage_complete","coverage_score","missing_spans","unsupported_atoms","atoms_to_split","atoms_to_merge","review_notes"],"properties":{"coverage_complete":{"type":"boolean"},"coverage_score":{"type":"number"},"missing_spans":{"type":"array","items":{"type":"string"}},"unsupported_atoms":{"type":"array","items":{"type":"string"}},"atoms_to_split":{"type":"array","items":{"type":"string"}},"atoms_to_merge":{"type":"array","items":{"type":"string"}},"review_notes":{"type":"array","items":{"type":"string"}}}}
    audit=_chat(settings,settings.requirement_auditor_model,"你是独立覆盖审计器。检查遗漏、扩写、错误合并和过度拆分，只输出JSON。\n"+json.dumps({"functional_description":description,"atoms":[{"id":x.indicator_id,"text":x.indicator_text,"evidence_spans":x.evidence_spans} for x in atoms]},ensure_ascii=False),audit_schema)
    for field in ("missing_spans","unsupported_atoms","atoms_to_split","atoms_to_merge","review_notes"):
        audit[field]=[str(item).strip()[:settings.audit_reason_max_chars] for item in audit.get(field,[]) if str(item).strip()]
    action_tokens=set(re.findall(r"(?:支持|显示|查看|新增|修改|删除|保存|发布|校验|提示|配置|管理)",description)); atom_text=" ".join(x.indicator_text for x in atoms)
    deterministic_missing=sorted(x for x in action_tokens if x not in atom_text)
    duplicates=len({x.indicator_text for x in atoms})!=len(atoms); unsupported=[x.indicator_id for x in atoms if not x.evidence_spans]
    complete=bool(audit["coverage_complete"] and not audit["missing_spans"] and not unsupported and not deterministic_missing and not duplicates)
    for atom in atoms: atom.audit_status="passed" if complete else "pending"
    return {"atoms":atoms,"model_audit":audit,"deterministic_audit":{"missing_action_tokens":deterministic_missing,"unsupported_atoms":unsupported,"duplicates":duplicates},"coverage_complete":complete,"coverage_score":1.0 if complete else min(float(audit.get("coverage_score",0)),.99)}
