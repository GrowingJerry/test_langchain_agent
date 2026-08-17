import pytest
from application.services.case_regeneration_service import CaseRegenerationService
from application.services.project_service import ProjectManager
from infrastructure.repositories.traceability_repository import TraceabilityRepository

pytestmark=pytest.mark.ollama

def test_real_model_single_case_regeneration_is_immutable_and_scoped(tmp_path,live_ollama,record_property):
    manager=ProjectManager(tmp_path/"workspace.db"); project_id=manager.create_project("Ollama单条重生成")["project_id"]
    manager.replace_requirements(project_id,[{"requirement_id":"REQ-PROFILE","title":"个人信息配置","description":"手机号必须为11位数字；保存结果需联机确认。"}])
    original={"case_id":"TC-PROFILE-1","project_id":project_id,"requirement_ids":["REQ-PROFILE"],"indicator_ids":["IND-MOBILE","IND-SAVE"],"case_name":"手机号合法保存","steps":["在个人信息配置页面的手机号输入框输入11位数字。","点击保存按钮。"],"expected":["手机号输入框显示输入内容。","页面进入保存处理状态，数据库持久化待联机验证。"],"need_human_confirm":True}
    manager.save_generated_case(project_id,original)
    service=CaseRegenerationService(manager,live_ollama); result=service.regenerate(project_id,"TC-PROFILE-1","只修改预期结果：不要编造数据库保存成功，明确标记待联机验证。")
    assert result["version"]["version_no"]==1 and result["revised"]["case_id"]==original["case_id"]
    assert result["revised"]["indicator_ids"]==original["indicator_ids"]
    assert len(result["revised"]["steps"])==len(result["revised"]["expected"])
    assert "联机" in str(result["revised"]["expected"])
    assert manager.list_generated_cases(project_id)[0]["case_json"]==original
    repository=TraceabilityRepository(manager.connections); repository.accept_version(project_id,"TC-PROFILE-1",1)
    assert manager.list_generated_cases(project_id)[0]["case_json"]==result["revised"]
    rollback=repository.rollback(project_id,"TC-PROFILE-1",1); assert rollback["version_no"]==2
    record_property("ollama_model",live_ollama.ollama_model); record_property("elapsed_seconds",result["elapsed_seconds"])
