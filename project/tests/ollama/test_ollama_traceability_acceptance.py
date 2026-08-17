import json, time, requests, pytest
from application.services.traceability_service import enforce_online_confirmation, validate_step_alignment

pytestmark=pytest.mark.ollama

def _chat(settings,prompt):
    case_schema={"type":"object","required":["case_id","indicator_ids","steps","expected","expected_source","need_human_confirm"],"properties":{"case_id":{"type":"string"},"indicator_ids":{"type":"array","minItems":1,"items":{"type":"string"}},"steps":{"type":"array","minItems":3,"maxItems":3,"items":{"type":"string","minLength":1}},"expected":{"type":"array","minItems":3,"maxItems":3,"items":{"type":"string","minLength":1}},"expected_source":{"type":"string"},"need_human_confirm":{"type":"boolean"}},"additionalProperties":True}
    schema={"type":"object","required":["cases"],"properties":{"cases":{"type":"array","minItems":4,"items":case_schema}}}
    last=None; correction=""
    for _ in range(settings.ollama_max_retries+1):
        try:
            response=requests.post(settings.ollama_base_url.rstrip("/")+"/api/chat",json={"model":settings.ollama_model,"stream":False,"think":False,"format":schema,"messages":[{"role":"user","content":prompt+correction}],"options":{"temperature":0,"num_predict":2400,"num_ctx":settings.ollama_num_ctx}},timeout=settings.ollama_timeout); response.raise_for_status(); data=json.loads(response.json()["message"]["content"])
            if len(data.get("cases",[]))<4: raise ValueError("用例少于4条")
            for case in data["cases"]: validate_step_alignment(case)
            return data,response.json()["model"]
        except Exception as exc:
            last=exc; correction="\n上次输出未通过校验："+str(exc)+"。每条用例的steps与expected必须数量完全相等，请逐条重新检查并输出完整JSON。"
    raise last

def test_personal_profile_structured_case_acceptance(live_ollama,record_property):
    indicators=["IND-VIEW:展示姓名手机号邮箱","IND-NAME:姓名不能为空","IND-MOBILE:手机号必须为11位数字","IND-EMAIL:邮箱必须符合格式","IND-SAVE:合法修改后保存","IND-TIP:保存成功提示"]
    prompt="""根据以下虚构测试夹具生成JSON对象，键cases为数组。至少生成合法保存、姓名为空、手机号格式错误、邮箱格式错误4条用例。每条必须有case_id、indicator_ids、steps数组、expected数组、expected_source、need_human_confirm。steps和expected数量相等；步骤具体写个人信息配置页面、姓名/手机号/邮箱输入框和保存按钮；数据库持久化不可离线确认，相关用例need_human_confirm=true且预期写待联机验证。不得输出Markdown。原子需求："""+json.dumps(indicators,ensure_ascii=False)
    started=time.monotonic(); data,model=_chat(live_ollama,prompt); cases=data["cases"]; assert len(cases)>=4
    covered=set()
    for case in cases:
        enforce_online_confirmation(case,{"IND-SAVE"}); validate_step_alignment(case); covered.update(case["indicator_ids"])
    assert {x.split(":")[0] for x in indicators}.issubset(covered)
    assert any(x.get("need_human_confirm") and "联机" in json.dumps(x,ensure_ascii=False) for x in cases)
    record_property("ollama_model",model); record_property("elapsed_seconds",round(time.monotonic()-started,3)); print(f"OLLAMA acceptance model={model} cases={len(cases)}")
