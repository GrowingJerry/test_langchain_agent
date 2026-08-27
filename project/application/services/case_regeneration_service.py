"""Single-case chat regeneration with immutable versions and coverage guards."""
from __future__ import annotations
import json, time
from uuid import uuid4
import requests
from infrastructure.llm.ollama_errors import NonRetryableSchemaError, raise_for_ollama_status, response_error_text
import logging
logger=logging.getLogger("test_agent.case_regeneration")
from application.services.traceability_service import enforce_online_confirmation
from domain.case_schema import MAX_CASE_STEPS, feedback_scope, merge_case, normalize_case, validate_case
from infrastructure.repositories.traceability_repository import TraceabilityRepository

class CaseRegenerationService:
    def __init__(self, manager, settings, session=requests):
        self.manager=manager; self.settings=settings; self.session=session; self.versions=TraceabilityRepository(manager.connections)

    def context(self, project_id:str, case_id:str)->dict:
        case_row=next((x for x in self.manager.list_generated_cases(project_id) if x.get("case_id")==case_id),None)
        if not case_row: raise KeyError("当前项目中不存在该用例")
        case=case_row.get("case_json") or {}; requirement_id=case_row.get("requirement_id","")
        requirement=self.manager.get_requirement(project_id,requirement_id) if requirement_id else None
        with self.manager.connections.connection() as conn:
            indicator_rows=[dict(x) for x in conn.execute("SELECT i.* FROM requirement_indicators i JOIN case_indicator_links l ON l.project_id=i.project_id AND l.indicator_id=i.indicator_id WHERE l.project_id=? AND l.case_id=?",(project_id,case_id))]
            element_rows=[dict(x) for x in conn.execute("SELECT e.* FROM html_elements e JOIN requirement_element_links l ON l.project_id=e.project_id AND l.confirmed_element_id=e.element_id WHERE l.project_id=? AND l.indicator_id IN (SELECT indicator_id FROM case_indicator_links WHERE project_id=? AND case_id=?)",(project_id,project_id,case_id))]
            observations=[dict(x) for x in conn.execute("SELECT * FROM html_observations WHERE project_id=? AND element_id IN (SELECT confirmed_element_id FROM requirement_element_links WHERE project_id=? AND indicator_id IN (SELECT indicator_id FROM case_indicator_links WHERE project_id=? AND case_id=?))",(project_id,project_id,project_id,case_id))]
        return {"case":case,"requirement":requirement or {},"indicators":indicator_rows,"html_elements":element_rows,"html_observations":observations,"generation_context":case.get("provenance",{})}

    def regenerate(self,project_id:str,case_id:str,feedback:str,operator:str="streamlit-user")->dict:
        context=self.context(project_id,case_id); original=context["case"]
        prompt="""你只修改一个测试用例。严格输出JSON对象，不要Markdown。保持case_id、project_id、需求来源和原子需求绑定；不得增加上下文外功能；步骤与预期结果数组必须一一对应；离线HTML观测不是正式需求；服务端或数据库结果标记待联机确认。用户仅要求某字段时其他字段保持不变。\n上下文："""+json.dumps(context,ensure_ascii=False)+"\n用户反馈："+feedback
        started=time.monotonic(); last_error=""
        original=normalize_case(original); step_count=len(original.get("test_steps") or []) or 1
        scope=feedback_scope(feedback); maximum=MAX_CASE_STEPS if scope == "steps" else step_count
        schema={"type":"object","required":["case_id","test_steps","expected_result"],"properties":{"case_id":{"type":"string"},"case_name":{"type":"string"},"test_steps":{"type":"array","minItems":1,"maxItems":maximum,"items":{"type":"string","minLength":1}},"expected_result":{"type":"array","minItems":1,"maxItems":maximum,"items":{"type":"string","minLength":1}},"need_human_confirm":{"type":"boolean"},"expected_source":{"type":"string"}},"additionalProperties":True}
        correction=""
        for attempt in range(self.settings.ollama_max_retries+1):
            try:
                response=self.session.post(self.settings.ollama_base_url.rstrip("/")+"/api/chat",json={"model":self.settings.ollama_model,"stream":False,"think":False,"format":schema,"messages":[{"role":"user","content":prompt+correction}],"options":{"temperature":0,"num_ctx":self.settings.ollama_num_ctx,"num_predict":self.settings.ollama_structured_num_predict}},timeout=self.settings.ollama_timeout)
                if not response.ok: logger.error("Case regeneration Ollama HTTP %s response=%s",response.status_code,response_error_text(response))
                raise_for_ollama_status(response); revised=json.loads(response.json()["message"]["content"])
                revised=merge_case(original,revised,feedback)
                if original.get("need_human_confirm") or "联机" in json.dumps(original,ensure_ascii=False):
                    revised["indicator_ids"]=original.get("indicator_ids") or revised.get("indicator_ids") or []
                    revised=enforce_online_confirmation(revised,revised["indicator_ids"])
                revised=validate_case(revised,case_id=case_id)
                if scope != "steps" and revised["test_steps"] != original["test_steps"]: raise ValueError("用户未要求修改步骤")
                if "联机" in feedback and "联机" not in json.dumps(revised["expected_result"],ensure_ascii=False): raise ValueError("模型输出未满足待联机验证要求")
                break
            except NonRetryableSchemaError: raise
            except (requests.RequestException,KeyError,TypeError,ValueError,json.JSONDecodeError) as exc:
                last_error=f"{type(exc).__name__}: {exc}"
                correction="\n上次输出未通过校验："+last_error+"。必须输出非空test_steps和expected_result，两者数量一致；如反馈涉及联机验证，expected_result必须明确体现。请重新输出完整JSON。"
                if attempt>=self.settings.ollama_max_retries: raise RuntimeError(f"单条用例重生成失败：{last_error}") from exc
        revised["case_id"]=original.get("case_id",case_id); revised["project_id"]=project_id
        for field in ("requirement_ids","indicator_ids","source_chunk_ids","requirement_hierarchy_path"):
            if field in original: revised[field]=original[field]
        revised=validate_case(revised,case_id=case_id)
        version=self.versions.create_case_version(project_id,case_id,revised,feedback=feedback,context=context,model_name=self.settings.ollama_model,operator=operator)
        logger.info("Case regeneration proposed project=%s case=%s version=%s changed=%s",project_id,case_id,version["version_no"],version["changed_fields"])
        conversation_id=f"CONV-{case_id}"
        with self.manager.connections.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO case_conversations(project_id,conversation_id,case_id) VALUES(?,?,?)",(project_id,conversation_id,case_id))
            for role,content in (("user",feedback),("assistant",json.dumps(revised,ensure_ascii=False))):
                conn.execute("INSERT INTO case_messages(project_id,message_id,conversation_id,role,content) VALUES(?,?,?,?,?)",(project_id,f"MSG-{uuid4().hex[:16]}",conversation_id,role,content))
            gaps=[dict(x) for x in conn.execute("SELECT indicator_id FROM case_indicator_links WHERE project_id=? AND case_id=? AND indicator_id NOT IN (SELECT indicator_id FROM case_indicator_links WHERE project_id=? AND case_id<>?)",(project_id,case_id,project_id,case_id)) if x["indicator_id"] not in revised.get("indicator_ids",[])]
        return {"original":original,"revised":revised,"version":version,"elapsed_seconds":round(time.monotonic()-started,3),"coverage_gap_warning":[x["indicator_id"] for x in gaps]}
