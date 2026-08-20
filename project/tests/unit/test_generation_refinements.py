import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from application.services.case_id_service import CaseIdService
from config.settings import Settings
from domain.rules.evidence_policy import EvidencePolicy
from domain.schemas.test_case import TestCase
from infrastructure.llm.stream_guard import GenerationTerminated, StreamGuard
from application.services.generation_package import estimate_generation_capacity
from domain.rules.element_position import position_evidence


class Connections:
    def __init__(self, conn): self.conn=conn
    @contextmanager
    def connection(self): yield self.conn

class Manager:
    def __init__(self):
        self.conn=sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE requirement_nodes(project_id,node_id,parent_id,identifier,ancestor_identifiers_json)")
        self.conn.executemany("INSERT INTO requirement_nodes VALUES(?,?,?,?,?)", [
            ("P","N1","","ZH_TYMH","[]"),("P","N2","N1","ZH_TYMH_XWMH",'["ZH_TYMH"]')])
        self.connections=Connections(self.conn); self.saved=[]
    def list_generated_cases(self, _): return [{"case_id":x} for x in self.saved]

def case(temp_id="TC-MODEL-001"):
    return TestCase(case_id=temp_id,title="新闻",objective="验证新闻",test_steps=["操作"],expected_results=["结果"],evaluation_criteria="结果可见")

def test_program_assigns_identifier_module_sequence_and_never_reuses():
    manager=Manager(); service=CaseIdService(manager, Settings())
    first=service.assign("P","ZH_TYMH_XWMH",[case(),case("random")])
    assert [x.case_id for x in first] == ["ZH_TYMH-XWMH-0001","ZH_TYMH-XWMH-0002"]
    manager.saved=[x.case_id for x in first]
    assert service.assign("P","ZH_TYMH_XWMH",[case()])[0].case_id == "ZH_TYMH-XWMH-0003"

def test_evidence_policy_varies_by_test_type():
    settings=Settings()
    assert EvidencePolicy.for_test_type("功能测试",settings).include_elements
    assert not EvidencePolicy.for_test_type("接口测试",settings).include_elements
    assert "不得由HTML推断" in EvidencePolicy.for_test_type("性能测试",settings).limitations[0]
    assert "攻击入口" in EvidencePolicy.for_test_type("安全测试",settings).limitations[0]

def test_repeated_digit_stream_is_terminated_with_partial_output():
    guard=StreamGuard(Settings(generation_max_digit_run=12))
    with pytest.raises(GenerationTerminated) as caught:
        guard.collect([{"message":{"content":"{\"x\":138111111111111111111}"}}])
    assert caught.value.reason == "repetition_detected"
    assert "138" in caught.value.partial_output

def test_client_cancellation_terminates_stream():
    with pytest.raises(GenerationTerminated) as caught:
        StreamGuard(Settings(), lambda: True).collect([{"message":{"content":"{"}}])
    assert caught.value.reason == "client_cancelled"

def test_internal_35b_configuration_changes_actual_context_values():
    settings=Settings.from_env({"OLLAMA_NUM_CTX":"131072","OLLAMA_NUM_PREDICT":"32768",
                                "GENERATION_CONTEXT_TOKEN_BUDGET":"90000","GENERATION_OUTPUT_TOKEN_RESERVE":"24000"})
    assert settings.ollama_num_ctx == 131072
    assert settings.ollama_structured_num_predict == 32768
    assert settings.generation_context_token_budget == 90000

def test_4669_token_large_case_request_is_blocked_at_2048_output_budget():
    capacity=estimate_generation_capacity(input_tokens=4669,case_count=20,num_ctx=8192,num_predict=2048,settings=Settings())
    assert capacity == {"input_tokens":4669,"expected_output_tokens":18350,
                        "available_output_tokens":2048,"case_count":20,"capacity_sufficient":False}

def test_position_phrase_comes_from_playwright_box():
    evidence=position_evidence({"x":1100,"y":20,"width":120,"height":40},{"width":1366,"height":768},"〖公告列表〗页面")
    assert evidence["relative_position"]=="右上角"
    assert evidence["position_source"]=="playwright_bbox+dom_landmark"
