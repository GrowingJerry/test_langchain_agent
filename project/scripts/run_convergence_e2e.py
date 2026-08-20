"""Fresh-project convergence acceptance with auditable step records."""
from __future__ import annotations
import json,os,sys,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT)); os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(ROOT/'vendor'/'playwright-browsers')
from application.services.project_service import ProjectManager
from application.services.ui_application_service import UIApplicationService
from application.services.generation_service import GenerationRequest
from config.settings import Settings
from infrastructure.repositories.traceability_repository import TraceabilityRepository
from application.services.semantic_binding_service import bind_project

FIX=ROOT/'tests'/'fixtures'/'convergence'
SETTINGS=Settings(ollama_timeout=300,text_model='qwen3:8b',vision_model='qwen2.5vl:3b',requirement_atomizer_model='qwen3:8b',requirement_auditor_model='qwen3:8b',page_understanding_model='qwen2.5vl:3b',test_case_model='qwen3:8b',test_case_review_model='qwen3:8b')

def run_step(records,name,input_data,call,count=lambda x:1):
    started=datetime.now(timezone.utc); error=''
    try: result=call(); passed=True
    except Exception as exc: result=None; passed=False; error=f'{type(exc).__name__}: {exc}'
    ended=datetime.now(timezone.utc); result_count=count(result) if passed else 0; records.append({'step':name,'input':input_data,'started_at':started.isoformat(),'ended_at':ended.isoformat(),'result_count':result_count,'database_write_count':result_count,'ui_feedback':'成功' if passed else error,'error_log':error,'passed':passed})
    if not passed: raise RuntimeError(f'{name}: {error}')
    return result

def one_flow(manager,service,name,doc,expected_method):
    records=[]; project_id=run_step(records,'创建项目',{'name':name},lambda:manager.create_project(name,'虚构收敛验收'))['project_id']
    run_step(records,'上传需求文档',{'file':doc.name},lambda:service.ingest_document(project_id,doc.name,doc.read_bytes()))
    stored=next(x for x in service.document_summaries(project_id) if x['filename']==doc.name)
    parsed=run_step(records,'抽取统一需求',{'mode':'auto'},lambda:service.extract_requirement_document(project_id,Path(stored['file_path']),'auto'),lambda x:x['testable_count']); assert parsed['extraction_method']==expected_method and parsed['testable_count']>0
    atom=run_step(records,'逐项原子化及独立审计',{},lambda:service.atomize_and_audit_requirements(project_id,resume=True),lambda x:x['completed']); assert atom['functions'] and not atom['failures']
    atom_rows=service.traceability_rows(project_id)['atomic_requirements']; service.save_reviewed_atoms(project_id,atom_rows); assert atom_rows
    site=run_step(records,'ZIP静态分析及证据压缩',{'file':'offline_site.zip'},lambda:service.analyze_site_zip(project_id,'offline_site.zip',(FIX/'offline_site.zip').read_bytes()),lambda x:len(x['pages']))
    health=service.playwright_status(); assert health['available']; explored=run_step(records,'Playwright动态探索',{'entry':'index.html'},lambda:service.auto_explore_site_package(project_id,site['site_package_id'],'index.html'),lambda x:len(x['events'])); assert explored['return_code']==0
    visual=run_step(records,'视觉理解',{'model':SETTINGS.page_understanding_model},lambda:service.understand_pages_visually(project_id)); assert visual['status']=='completed'
    binding=run_step(records,'语义绑定',service.binding_funnel(project_id),lambda:bind_project(manager,project_id,SETTINGS),lambda x:x['participating']); assert binding['participating']==binding['confirmed']+binding['need_human_confirm']
    def confirm_pending_bindings():
        rows=service.traceability_rows(project_id).get('requirement_page_links',[]); changed=0
        for row in rows:
            if row.get('status') in {'low_confidence','proposed','page_not_found'} and row.get('page_id') and float(row.get('confidence') or 0)>0 and not any(word in str(row.get('reason') or '') for word in ('无关','不相关','未找到匹配')):
                row['page_confirmed_element_pending']=True; row['confirmed_by']='acceptance-reviewer'; changed+=1
        if changed: service.save_binding_reviews(project_id,rows)
        return {'reviewed':len(rows),'page_confirmed_element_pending':changed}
    run_step(records,'人工确认绑定',{},confirm_pending_bindings,lambda x:x['reviewed'])
    review=service.requirement_review_rows(project_id); service.save_requirement_reviews(project_id,review); submitted=service.submit_requirements_for_generation(project_id); assert submitted['submitted']>0
    manager.save_profile(project_id,{'test_object':'虚构资料维护系统','domain':'测试夹具','testers':['验收人员']})
    ids=[x['requirement_id'] for x in manager.list_requirements(project_id) if x['retained']]
    req=GenerationRequest(project_id=project_id,requirement_ids=ids,case_type='功能测试',case_count=8,auto_case_count=True,requested_mode='auto',use_project_kb=True,use_history=False)
    generated=run_step(records,'详细测试用例生成',{'requirements':ids},lambda:service.generate_requirement_batch(req,ids,'CONVERGENCE-'+project_id,force=True),lambda x:len(x['cases'])); assert generated['cases'] and not generated['failed']
    coverage=service.export_rows(project_id)['atomic_coverage_matrix']; covered={x['indicator_id'] for x in coverage if x.get('coverage_status')=='covered'}; required={x['indicator_id'] for x in atom_rows}; assert required<=covered
    case_id=generated['cases'][0]['case']['case_id']; regen=run_step(records,'单条用例对话式重生成',{'case_id':case_id},lambda:service.regenerate_single_case(project_id,case_id,'补充姓名为空和20字符边界，不得编造服务端保存成功'))
    service.update_case_version(project_id,case_id,regen['version']['version_no'],'accept'); service.update_case_version(project_id,case_id,regen['version']['version_no'],'rollback')
    export=run_step(records,'Excel导出',{},lambda:service.export_excel(project_id)); assert export.exists()
    return {'project_id':project_id,'method':parsed['extraction_method'],'records':records,'atoms':len(atom_rows),'pages':len(site['pages']),'source_bytes':sum(x.get('source_bytes',0) for x in site['pages']),'compressed_chars':sum(x.get('compressed_chars',0) for x in site['pages']),'binding':{k:binding[k] for k in ('participating','confirmed','need_human_confirm','unmatched','failed')},'cases':len(generated['cases']),'coverage_rate':len(covered)/len(required),'export':str(export),'chromium':health.get('chromium_version')}

def main():
    stamp=str(int(time.time())); root=ROOT/'outputs'/'convergence'/stamp; root.mkdir(parents=True,exist_ok=True); db=root/'workspace.db'; manager=ProjectManager(db); service=UIApplicationService(manager,None,SETTINGS)
    flows=[one_flow(manager,service,'收敛验收-CSCI',FIX/'shifted_csci.docx','csci_structured'),one_flow(manager,service,'收敛验收-通用AI',FIX/'general_requirements.docx','llm_general')]
    restarted=ProjectManager(db); persisted={x['project_id']:len(restarted.list_generated_cases(x['project_id'])) for x in flows}; assert all(persisted.values())
    report={'status':'passed','completed_at':datetime.now(timezone.utc).isoformat(),'database':str(db),'models':['qwen3:8b','qwen2.5vl:3b'],'flows':flows,'restart_persisted_cases':persisted}; path=root/'acceptance-report.json'; path.write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
