"""Persist semantic requirement-to-page/element bindings from compact evidence."""
from __future__ import annotations
import json,re,logging,time
from hashlib import sha256
from pathlib import Path
from typing import Any
import requests
from config.settings import Settings
from infrastructure.database.json_codec import loads_json,dumps_json
from infrastructure.llm.visual_client import OllamaVisualClient
from infrastructure.llm.ollama_errors import NonRetryableSchemaError, raise_for_ollama_status, response_error_text

logger=logging.getLogger("test_agent.semantic_binding")

def semantic_binding_schema(settings:Settings)->dict[str,Any]:
    """Production schema kept grammar-safe for Ollama; text bounds are enforced in Python."""
    return {"type":"object","required":["page_id","confidence","reason","element_ids","need_human_confirm"],"properties":{"page_id":{"type":"string"},"confidence":{"type":"number"},"reason":{"type":"string"},"element_ids":{"type":"array","maxItems":settings.binding_max_element_ids,"items":{"type":"string"}},"need_human_confirm":{"type":"boolean"}}}

def _semantic_chat(settings:Settings,schema:dict[str,Any],payload:dict[str,Any])->dict[str,Any]:
    last=None
    for attempt in range(settings.ollama_max_retries+1):
        try:
            response=requests.post(settings.ollama_base_url.rstrip("/")+"/api/chat",json={"model":settings.text_model,"stream":False,"think":False,"format":schema,"messages":[{"role":"user","content":"依据需求语义、简化DOM和真实观测选择页面及元素。关键词仅是候选，不得编造ID。只输出完整JSON。\n"+json.dumps(payload,ensure_ascii=False)}],"options":{"temperature":settings.ollama_temperature,"num_predict":settings.binding_model_num_predict,"num_ctx":settings.ollama_num_ctx}},timeout=settings.binding_model_timeout)
            if not response.ok: logger.error("Semantic binding Ollama HTTP %s response=%s",response.status_code,response_error_text(response))
            raise_for_ollama_status(response); decision=json.loads(response.json()["message"]["content"])
            if not isinstance(decision,dict) or not all(key in decision for key in ("page_id","confidence","reason","element_ids","need_human_confirm")): raise ValueError("semantic binding JSON is incomplete")
            decision["reason"]=str(decision.get("reason") or "").strip()[:settings.binding_reason_max_chars]
            return decision
        except NonRetryableSchemaError:
            logger.exception("Semantic binding stopped: non_retryable_schema_error")
            raise
        except Exception as exc:
            last=exc; logger.exception("Semantic binding model attempt %s failed",attempt+1)
    raise RuntimeError(f"Semantic binding failed after limited retries: {type(last).__name__}: {last}")

def _tokens(text:str)->set[str]: return set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_]+",text.lower()))

def bind_project(manager,project_id:str,settings:Settings)->dict[str,Any]:
    with manager.connections.connection() as conn:
        nodes=[dict(x) for x in conn.execute("SELECT * FROM requirement_nodes WHERE project_id=? AND testable=1 AND enabled=1 AND deleted_at IS NULL",(project_id,))]
        indicators=[dict(x) for x in conn.execute("SELECT * FROM requirement_indicators WHERE project_id=?",(project_id,))]
        pages=[dict(x) for x in conn.execute("SELECT * FROM html_pages WHERE project_id=?",(project_id,))]
        elements=[dict(x) for x in conn.execute("SELECT * FROM html_elements WHERE project_id=? ORDER BY page_id,element_id LIMIT ?",(project_id,settings.html_max_total_elements))]
        observations=[dict(x) for x in conn.execute("SELECT * FROM html_observations WHERE project_id=? ORDER BY observed_at DESC LIMIT ?",(project_id,settings.playwright_max_observations))]
        summaries={x['page_id']:loads_json(x['summary_json'],{}) for x in map(dict,conn.execute("SELECT page_id,summary_json FROM html_page_summaries WHERE project_id=?",(project_id,)))}
    by_page={p["page_id"]:[] for p in pages}
    for element in elements:
        bucket=by_page.setdefault(element["page_id"],[])
        if len(bucket)<settings.html_max_elements_per_page: bucket.append(loads_json(element["element_json"],{}))
    observation_text=" ".join(json.dumps(loads_json(x.get("result_json"),{}),ensure_ascii=False) for x in observations)
    results=[]
    with manager.connections.transaction() as conn:
        for node in nodes:
            sections=loads_json(node.get("sections_json"),{}); requirement_text=" ".join([node["name"],*sections.values()]); req_tokens=_tokens(requirement_text)
            candidates=[]
            for page in pages:
                controls=[]
                for item in by_page.get(page["page_id"],[]):
                    attrs=item.get("attributes") or {}
                    controls.append({"element_id":item.get("element_id"),"tag":item.get("tag"),"element_type":item.get("element_type"),"text":str(item.get("text") or "")[:120],"label":str(item.get("label") or "")[:120],"attributes":{key:str(attrs.get(key) or "")[:120] for key in ("id","name","placeholder","role","aria-label") if attrs.get(key)}})
                summary=summaries.get(page['page_id'],{}); page_text=" ".join([page.get("title") or "",page.get("page_path") or "",str(summary.get('visible_text_summary') or '')[:4000],json.dumps(controls,ensure_ascii=False)])
                hits=sorted(req_tokens&_tokens(page_text)); score=len(hits)/max(1,len(req_tokens)); candidates.append({"page_id":page["page_id"],"title":page.get("title"),"path":page.get("page_path"),"visible_text_summary":str(summary.get('visible_text_summary') or '')[:2000],"score":score,"hits":hits,"controls":controls})
            candidates.sort(key=lambda x:x["score"],reverse=True); compact=[{k:v for k,v in x.items() if k!="controls"} for x in candidates[:settings.binding_max_candidate_pages]]
            schema=semantic_binding_schema(settings)
            payload={"requirement":{"function_id":node["identifier"],"hierarchy":loads_json(node.get("hierarchy_path_json"),[]),"sections":sections},"candidate_pages":candidates[:settings.binding_max_candidate_pages],"playwright_observation":observation_text[:settings.playwright_max_observation_text_chars]}
            try: decision=_semantic_chat(settings,schema,payload)
            except Exception as exc:
                logger.exception("Binding failed project=%s function=%s",project_id,node['identifier'])
                results.append({"function_id":node["identifier"],"status":"failed","page_id":"","element_ids":[],"confidence":0,"reason":f"{type(exc).__name__}: {exc}"}); continue
            valid_page=next((x for x in candidates if x["page_id"]==decision.get("page_id")),None); valid_elements={x.get("element_id") for x in (valid_page or {}).get("controls",[])}; selected=[x for x in decision.get("element_ids",[]) if x in valid_elements]
            confidence=max(0,min(1,float(decision.get("confidence",0)))); reason=str(decision.get("reason") or ""); unrelated=confidence<=0 or any(word in reason for word in ("无关","不相关","未找到匹配")); status="unmatched" if unrelated else ("confirmed" if valid_page and selected and confidence>=settings.binding_auto_confirm_threshold and not decision.get("need_human_confirm") else ("low_confidence" if valid_page else "page_not_found"))
            persisted_page_id=decision.get("page_id") if valid_page and not unrelated else ""
            link_id="RPL-"+sha256((project_id+node["node_id"]).encode()).hexdigest()[:16]
            conn.execute("INSERT OR REPLACE INTO requirement_page_links(project_id,link_id,function_id,page_id,confidence,reason,status,need_human_confirm,model_name,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?)",(project_id,link_id,node["identifier"],persisted_page_id,confidence,reason,status,int(status!="confirmed"),settings.text_model,dumps_json({"candidate_recall":compact,"model_decision":decision})))
            for indicator in [x for x in indicators if x["function_id"]==node["identifier"]]:
                el=selected[0] if selected else ""; el_link="REL-"+sha256((indicator["indicator_id"]+el).encode()).hexdigest()[:16]
                element_status="confirmed" if status=="confirmed" and el else ("unmatched" if status=="unmatched" else "proposed")
                conn.execute("INSERT OR REPLACE INTO requirement_element_links(project_id,link_id,indicator_id,page_id,confirmed_element_id,candidates_json,confidence,reason,status,need_human_confirm) VALUES(?,?,?,?,?,?,?,?,?,?)",(project_id,el_link,indicator["indicator_id"],persisted_page_id,el if element_status=="confirmed" else "",dumps_json(selected),confidence,reason,element_status,int(element_status!="confirmed")))
            results.append({"function_id":node["identifier"],"status":status,"page_id":persisted_page_id,"element_ids":selected if status=="confirmed" else [],"confidence":confidence,"reason":reason})
    confirmed=sum(x["status"]=="confirmed" for x in results); failed=sum(x["status"]=="failed" for x in results); pending=sum(x["status"] not in {"confirmed","failed"} for x in results)
    return {"bindings":results,"participating":len(nodes),"confirmed":confirmed,"need_human_confirm":pending+failed,"unmatched":sum(x["status"] in {"page_not_found","unmatched"} for x in results),"failed":failed,"model":settings.text_model}
