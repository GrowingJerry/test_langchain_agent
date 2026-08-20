import time,threading,json
import application.services.generation_task_registry as registry
from application.services.generation_task_registry import GenerationTaskStore,submit_generation,request_cancel

def wait_terminal(store,task_id,timeout=3):
    end=time.time()+timeout
    while time.time()<end:
        state=store.state(task_id)
        if state.get('status') in {'completed','failed','cancelled'}: return state
        time.sleep(.01)
    raise AssertionError(store.state(task_id))

def test_events_are_ordered_persistent_and_completion_is_terminal(tmp_path):
    store=GenerationTaskStore(tmp_path)
    def work(cancelled,emit):
        emit('status','正在调用Agent'); emit('reasoning','分析'); emit('token','{"cases":'); emit('progress','REQ 已生成并保存',requirement_id='REQ',index=1,total=1)
        return {'cases':[{'case':{'case_id':'REQ-X-0001'}}],'completed':['REQ'],'skipped':[],'failed':[],'diagnostic_runs':[{'generation_mode':'agent','diagnostic_log_path':'run.log'}]}
    task=submit_generation(store,work,project_id='P',requirement_id='REQ',model='qwen3:8b',mode='auto',total=1)
    state=wait_terminal(store,task); events=store.events_after(task,0)
    assert state['status']=='completed' and state['persisted_case_count']==1
    assert [x['sequence'] for x in events]==list(range(1,len(events)+1))
    assert {x['kind'] for x in events}>={'status','reasoning','token','progress','done'}
    assert store.result(task)['cases'][0]['case']['case_id']=='REQ-X-0001'
    restored=GenerationTaskStore(tmp_path)
    assert restored.state(task)['status']=='completed' and restored.events_after(task,0)==events

def test_cancel_request_reaches_cancelled_and_saves_no_result(tmp_path):
    store=GenerationTaskStore(tmp_path)
    def work(cancelled,emit):
        while not cancelled(): time.sleep(.01)
        return {'cases':[],'completed':[],'skipped':[],'failed':[],'cancelled':True}
    task=submit_generation(store,work,project_id='P',requirement_id='REQ',model='qwen3:8b',mode='auto',total=1)
    assert request_cancel(store,task)
    state=wait_terminal(store,task)
    assert state['status']=='cancelled' and state['persisted_case_count']==0
    assert state['termination_reason']=='client_cancelled'

def test_recover_marks_orphan_running_as_application_restarted(tmp_path):
    store=GenerationTaskStore(tmp_path); directory=store.task_dir('RUN-OLD'); directory.mkdir()
    (directory/'state.json').write_text('{"task_id":"RUN-OLD","project_id":"P","status":"running","last_event_sequence":0}',encoding='utf-8')
    store.recover_interrupted(); state=store.state('RUN-OLD')
    assert state['status']=='failed' and state['termination_reason']=='application_restarted'

def test_repetition_failure_never_remains_running(tmp_path):
    store=GenerationTaskStore(tmp_path)
    def work(cancelled,emit): raise RuntimeError('termination_reason=repetition_detected')
    task=submit_generation(store,work,project_id='P',requirement_id='REQ',model='qwen3:8b',mode='auto',total=1)
    state=wait_terminal(store,task)
    assert state['status']=='failed' and state['termination_reason']=='repetition_detected'

def test_atomic_replace_retries_permission_error_and_cleans_unique_temp(tmp_path,monkeypatch):
    target=tmp_path/'state.json'; original=registry.os.replace; calls=[]
    def flaky(src,dst):
        calls.append(src)
        if len(calls)==1: raise PermissionError(5,'denied')
        return original(src,dst)
    monkeypatch.setattr(registry.os,'replace',flaky); registry._atomic(target,{'ok':True})
    assert json.loads(target.read_text('utf-8'))=={'ok':True} and len(calls)==2
    assert '.tmp' in calls[0].name and not list(tmp_path.glob('*.tmp'))

def test_atomic_replace_succeeds_after_multiple_permission_errors(tmp_path,monkeypatch):
    target=tmp_path/'state.json'; original=registry.os.replace; attempts=0
    def flaky(src,dst):
        nonlocal attempts; attempts+=1
        if attempts<5: raise PermissionError(5,'denied')
        return original(src,dst)
    monkeypatch.setattr(registry.os,'replace',flaky); registry._atomic(target,{'attempts':5})
    assert attempts==5 and json.loads(target.read_text('utf-8'))['attempts']==5

def test_concurrent_updates_and_high_frequency_reads_are_consistent(tmp_path):
    store=GenerationTaskStore(tmp_path); directory=store.task_dir('RUN-C'); directory.mkdir(); registry._atomic(directory/'state.json',{'task_id':'RUN-C','status':'running','last_event_sequence':0})
    errors=[]
    def writer(index):
        try:
            for value in range(30): store.update('RUN-C',**{f'w{index}':value})
        except Exception as exc: errors.append(exc)
    threads=[threading.Thread(target=writer,args=(i,)) for i in range(5)]
    for thread in threads: thread.start()
    while any(thread.is_alive() for thread in threads):
        state=store.state('RUN-C'); assert state.get('task_id')=='RUN-C'
    for thread in threads: thread.join()
    assert not errors and all(store.state('RUN-C')[f'w{i}']==29 for i in range(5))

def test_event_persistence_failure_does_not_kill_successful_work(tmp_path,monkeypatch):
    store=GenerationTaskStore(tmp_path); original=store.emit
    monkeypatch.setattr(store,'emit',lambda *a,**k:(_ for _ in ()).throw(PermissionError(5,'event denied')))
    def work(cancelled,emit): emit('token','ok'); return {'cases':[{'case':{'case_id':'REQ-X-0001'}}],'completed':['REQ'],'skipped':[],'failed':[]}
    task=submit_generation(store,work,project_id='P',requirement_id='REQ',model='qwen3:8b',mode='direct',total=1)
    state=wait_terminal(store,task)
    assert state['status']=='completed' and state['persisted_case_count']==1
    assert state['process_log_incomplete'] is True
