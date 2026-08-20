import pytest
from config.settings import Settings
from application.services.semantic_binding_service import _semantic_chat, semantic_binding_schema
from infrastructure.llm.ollama_errors import NonRetryableSchemaError

class Response:
    ok=False; status_code=500
    text='error parsing grammar: number of repetitions exceeds sane defaults; failed to initialize grammar'
    def raise_for_status(self): raise AssertionError('classified before generic HTTP error')

def test_binding_reason_has_no_schema_max_length_and_has_python_bound(monkeypatch):
    settings=Settings(binding_reason_max_chars=500)
    schema=semantic_binding_schema(settings)
    assert schema['properties']['reason']=={'type':'string'}
    assert settings.generation_max_field_chars != settings.binding_reason_max_chars

def test_schema_grammar_error_is_non_retryable(monkeypatch):
    calls=[]
    monkeypatch.setattr('application.services.semantic_binding_service.requests.post',lambda *a,**k:(calls.append(1) or Response()))
    with pytest.raises(NonRetryableSchemaError,match='non_retryable_schema_error'):
        _semantic_chat(Settings(ollama_max_retries=3),semantic_binding_schema(Settings()),{})
    assert len(calls)==1

def test_reason_is_trimmed_after_model_response(monkeypatch):
    class OK:
        ok=True; status_code=200; text=''
        def json(self): return {'message':{'content':__import__('json').dumps({'page_id':'P','confidence':.5,'reason':' x '*1000,'element_ids':[],'need_human_confirm':True})}}
    monkeypatch.setattr('application.services.semantic_binding_service.requests.post',lambda *a,**k:OK())
    result=_semantic_chat(Settings(binding_reason_max_chars=80),semantic_binding_schema(Settings()),{})
    assert len(result['reason'])==80 and not result['reason'].startswith(' ')
