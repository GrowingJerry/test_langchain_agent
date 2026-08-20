import time
import pytest
from application.services.semantic_binding_service import _semantic_chat, semantic_binding_schema

pytestmark=pytest.mark.ollama

CASES=[
 ("abstract",{"requirement":{"function_id":"ZH_TYMH","sections":{"功能描述":"提供统一门户综合信息服务"}},"candidate_pages":[{"page_id":"PAGE-PORTAL","title":"统一门户","path":"index.html","visible_text_summary":"新闻 个人中心 通知","controls":[]}],"playwright_observation":""}),
 ("related",{"requirement":{"function_id":"ZH_TYMH_XWMH","sections":{"功能描述":"新增新闻公告"}},"candidate_pages":[{"page_id":"PAGE-NEWS","title":"新闻门户","path":"news.html","visible_text_summary":"公告列表 新增公告 标题 保存","controls":[{"element_id":"EL-ADD","tag":"button","text":"新增公告"}]}],"playwright_observation":"点击新增公告后打开公告编辑对话框"}),
 ("unrelated",{"requirement":{"function_id":"NEWS","sections":{"功能描述":"发布新闻公告"}},"candidate_pages":[{"page_id":"PAGE-PROFILE","title":"个人信息配置","path":"profile.html","visible_text_summary":"姓名 手机号 保存个人信息","controls":[{"element_id":"EL-PROFILE-SAVE","tag":"button","text":"保存个人信息"}]}],"playwright_observation":""}),
]

@pytest.mark.parametrize("scenario,payload",CASES)
def test_production_binding_schema_with_qwen3_8b(live_ollama,scenario,payload,record_property):
    schema=semantic_binding_schema(live_ollama)
    assert schema["properties"]["reason"]=={"type":"string"}
    started=time.monotonic(); decision=_semantic_chat(live_ollama,schema,payload); elapsed=time.monotonic()-started
    valid_pages={x["page_id"] for x in payload["candidate_pages"]}
    valid_elements={x["element_id"] for page in payload["candidate_pages"] for x in page.get("controls",[])}
    assert decision["page_id"] in valid_pages|{""}
    assert set(decision["element_ids"])<=valid_elements
    assert len(decision["reason"])<=live_ollama.binding_reason_max_chars
    if scenario=="related": assert decision["page_id"]=="PAGE-NEWS"
    if scenario=="unrelated": assert decision["confidence"]<live_ollama.binding_auto_confirm_threshold or decision["need_human_confirm"]
    record_property("scenario",scenario); record_property("model",live_ollama.text_model); record_property("elapsed_seconds",round(elapsed,3))
