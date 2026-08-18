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

logger=logging.getLogger("test_agent.semantic_binding")

def _semantic_chat(settings:Settings,schema:dict[str,Any],payload:dict[str,Any])->dict[str,Any]:
    last=None
    for attempt in range(settings.ollama_max_retries+1):
        try:
            response=requests.post(settings.ollama_base_url.rstrip("/")+"/api/chat",json={"model":settings.text_model,"stream":False,"think":False,"format":schema,"messages":[{"role":"user","content":"依据需求语义、简化DOM和真实观测选择页面及元素。关键词仅是候选，不得编造ID。只输出完整JSON。\n"+json.dumps(payload,ensure_ascii=False)}],"options":{"temperature":0,"num_predict":2000}},timeout=settings.ollama_timeout)
            response.raise_for_status(); decision=json.loads(response.json()["message"]["content"])
            if not isinstance(decision,dict) or not all(key in decision for key in ("page_id","confidence","reason","element_ids","need_human_confirm")): raise ValueError("semantic binding JSON is incomplete")
            return decision
        except Exception as exc:
            last=exc; logger.exception("Semantic binding model attempt %s failed",attempt+1)
    raise RuntimeError(f"Semantic binding failed after limited retries: {type(last).__name__}: {last}")

def _tokens(text:str)->set[str]: return set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_]+",text.lower()))

def bind_project(manager,project_id:str,settings:Settings)->dict[str,Any]:
    with manager.connections.connection() as conn:
        nodes=[dict(x) for x in conn.execute("SELECT * FROM requirement_nodes WHERE project_id=? AND testable=1",(project_id,))]
        indicators=[dict(x) for x in conn.execute("SELECT * FROM requirement_indicators WHERE project_id=?",(project_id,))]
        pages=[dict(x) for x in conn.execute("SELECT * FROM html_pages WHERE project_id=?",(project_id,))]
        elements=[dict(x) for x in conn.execute("SELECT * FROM html_elements WHERE project_id=? ORDER BY page_id,element_id LIMIT 2000",(project_id,))]
        observations=[dict(x) for x in conn.execute("SELECT * FROM html_observations WHERE project_id=? ORDER BY observed_at DESC LIMIT 50",(project_id,))]
    by_page={p["page_id"]:[] for p in pages}
    for element in elements:
        bucket=by_page.setdefault(element["page_id"],[])
        if len(bucket)<100: bucket.append(loads_json(element["element_json"],{}))
    observation_text=" ".join(json.dumps(loads_json(x.get("result_json"),{}),ensure_ascii=False) for x in observations)
    results=[]
    with manager.connections.transaction() as conn:
        for node in nodes:
            sections=loads_json(node.get("sections_json"),{}); requirement_text=" ".join([node["name"],*sections.values()]); req_tokens=_tokens(requirement_text)
            candidates=[]
            for page in pages:
                controls=[]
                for item in by_page.get(page["page_id"],[])[:30]:
                    attrs=item.get("attributes") or {}
                    controls.append({"element_id":item.get("element_id"),"tag":item.get("tag"),"element_type":item.get("element_type"),"text":str(item.get("text") or "")[:120],"label":str(item.get("label") or "")[:120],"attributes":{key:str(attrs.get(key) or "")[:120] for key in ("id","name","placeholder","role","aria-label") if attrs.get(key)}})
                page_text=" ".join([page.get("title") or "",page.get("page_path") or "",json.dumps(controls,ensure_ascii=False)])
                hits=sorted(req_tokens&_tokens(page_text)); score=len(hits)/max(1,len(req_tokens)); candidates.append({"page_id":page["page_id"],"title":page.get("title"),"path":page.get("page_path"),"score":score,"hits":hits,"controls":controls})
            candidates.sort(key=lambda x:x["score"],reverse=True); compact=[{k:v for k,v in x.items() if k!="controls"} for x in candidates[:5]]
            schema={"type":"object","required":["page_id","confidence","reason","element_ids","need_human_confirm"],"properties":{"page_id":{"type":"string"},"confidence":{"type":"number"},"reason":{"type":"string","maxLength":500},"element_ids":{"type":"array","maxItems":10,"items":{"type":"string"}},"need_human_confirm":{"type":"boolean"}}}
            payload={"requirement":{"function_id":node["identifier"],"hierarchy":loads_json(node.get("hierarchy_path_json"),[]),"sections":sections},"candidate_pages":candidates[:5],"playwright_observation":observation_text[:10000]}
            decision=_semantic_chat(settings,schema,payload)
            valid_page=next((x for x in candidates if x["page_id"]==decision.get("page_id")),None); valid_elements={x.get("element_id") for x in (valid_page or {}).get("controls",[])}; selected=[x for x in decision.get("element_ids",[]) if x in valid_elements]
            confidence=max(0,min(1,float(decision.get("confidence",0)))); status="confirmed" if valid_page and confidence>=.8 and not decision.get("need_human_confirm") else ("low_confidence" if valid_page else "page_not_found")
            link_id="RPL-"+sha256((project_id+node["node_id"]).encode()).hexdigest()[:16]
            conn.execute("INSERT OR REPLACE INTO requirement_page_links(project_id,link_id,function_id,page_id,confidence,reason,status,need_human_confirm,model_name,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?)",(project_id,link_id,node["identifier"],decision.get("page_id") if valid_page else "",confidence,decision.get("reason",""),status,int(status!="confirmed"),settings.text_model,dumps_json({"candidate_recall":compact,"model_decision":decision})))
            for indicator in [x for x in indicators if x["function_id"]==node["identifier"]]:
                el=selected[0] if selected else ""; el_link="REL-"+sha256((indicator["indicator_id"]+el).encode()).hexdigest()[:16]
                conn.execute("INSERT OR REPLACE INTO requirement_element_links(project_id,link_id,indicator_id,page_id,confirmed_element_id,candidates_json,confidence,reason,status,need_human_confirm) VALUES(?,?,?,?,?,?,?,?,?,?)",(project_id,el_link,indicator["indicator_id"],decision.get("page_id") if valid_page else "",el,dumps_json(selected),confidence,decision.get("reason",""),status,int(status!="confirmed" or not el)))
            results.append({"function_id":node["identifier"],"status":status,"page_id":decision.get("page_id") if valid_page else "","element_ids":selected,"confidence":confidence,"reason":decision.get("reason","")})
    return {"bindings":results,"confirmed":sum(x["status"]=="confirmed" for x in results),"need_human_confirm":sum(x["status"]!="confirmed" for x in results),"model":settings.text_model}
