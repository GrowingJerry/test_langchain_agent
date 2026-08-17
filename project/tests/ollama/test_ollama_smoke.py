import time, requests, pytest

pytestmark=pytest.mark.ollama

def test_api_chat_smoke_records_model_and_latency(live_ollama,record_property):
    started=time.monotonic(); response=requests.post(live_ollama.ollama_base_url.rstrip("/")+"/api/chat",json={"model":live_ollama.ollama_model,"stream":False,"think":False,"format":"json","messages":[{"role":"user","content":"仅输出JSON：{\"status\":\"ok\"}"}],"options":{"temperature":0,"num_predict":512}},timeout=live_ollama.ollama_timeout); elapsed=time.monotonic()-started
    response.raise_for_status(); payload=response.json(); assert payload["model"].split(":")[0] in live_ollama.ollama_model
    assert "message" in payload and payload["message"]["content"]
    record_property("ollama_model",payload["model"]); record_property("elapsed_seconds",round(elapsed,3)); print(f"OLLAMA model={payload['model']} elapsed={elapsed:.3f}s")
