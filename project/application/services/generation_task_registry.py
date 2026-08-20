"""Persistent event stream and lifecycle for cancellable background generation."""
from __future__ import annotations
import json, threading, time, uuid, os, logging
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

TERMINAL={"completed","failed","cancelled"}; ACTIVE={"created","running","cancel_requested"}
_executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix="case-generation")
logger=logging.getLogger("test_agent.generation_tasks")
_locks_guard=threading.Lock(); _run_locks:dict[str,threading.RLock]={}; _events:dict[str,threading.Event]={}; _futures:dict[str,Future]={}
_replace_delays=(.05,.1,.2,.4,.8)

def _now()->str: return datetime.now(timezone.utc).isoformat()
def _run_lock(task_id:str)->threading.RLock:
    with _locks_guard: return _run_locks.setdefault(task_id,threading.RLock())
def _atomic(path:Path,value:Any)->None:
    temp=path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w",encoding="utf-8",newline="\n") as stream:
            json.dump(value,stream,ensure_ascii=False,default=str,indent=2); stream.flush(); os.fsync(stream.fileno())
        for attempt,delay in enumerate((*_replace_delays,0)):
            try: os.replace(temp,path); return
            except PermissionError:
                if attempt>=len(_replace_delays): raise
                time.sleep(delay)
    finally:
        try: temp.unlink(missing_ok=True)
        except OSError: logger.exception("Failed to clean generation task temp file path=%s",temp)

class GenerationTaskStore:
    def __init__(self,root:Path): self.root=root; root.mkdir(parents=True,exist_ok=True)
    def task_dir(self,task_id:str)->Path: return self.root/task_id
    def state(self,task_id:str)->dict[str,Any]:
        path=self.task_dir(task_id)/"state.json"
        for attempt,delay in enumerate((0,.02,.05,.1,.2)):
            try:
                with path.open("r",encoding="utf-8") as stream: raw=stream.read()
                return json.loads(raw)
            except FileNotFoundError:
                if attempt==4: return {}
            except (PermissionError,json.JSONDecodeError):
                if attempt==4: logger.exception("Failed to read generation task state task_id=%s",task_id); return {}
            if delay: time.sleep(delay)
        return {}
    def update(self,task_id:str,**changes:Any)->dict[str,Any]:
        with _run_lock(task_id):
            value=self.state(task_id); value.update(changes); _atomic(self.task_dir(task_id)/"state.json",value); return value
    def emit(self,task_id:str,kind:str,content:str,**extra:Any)->dict[str,Any]:
        with _run_lock(task_id):
            state=self.state(task_id); sequence=int(state.get("last_event_sequence") or 0)+1
            event={"sequence":sequence,"run_id":task_id,"requirement_id":state.get("requirement_id","") ,"kind":kind,"content":str(content),"created_at":_now(),**extra}
            with (self.task_dir(task_id)/"events.jsonl").open("a",encoding="utf-8") as stream: stream.write(json.dumps(event,ensure_ascii=False,default=str)+"\n")
            changes={"last_event_sequence":sequence,"current_stage":str(content)[:500]}
            if extra.get("requirement_id"): changes["requirement_id"]=extra["requirement_id"]
            if kind=="progress" and ("已生成并保存" in str(content) or "断点续跑跳过" in str(content)):
                changes["completed_requirements"]=min(int(state.get("total") or 1),int(state.get("completed_requirements") or 0)+1)
            self.update(task_id,**changes)
            return event
    def safe_update(self,task_id:str,**changes:Any)->dict[str,Any]:
        try: return self.update(task_id,**changes)
        except Exception:
            self._record_persistence_error(task_id,"state update",changes); return self.state(task_id)
    def safe_emit(self,task_id:str,kind:str,content:str,**extra:Any)->dict[str,Any]:
        try: return self.emit(task_id,kind,content,**extra)
        except Exception:
            self._record_persistence_error(task_id,"event append",{"kind":kind,"content":content}); return {}
    def _record_persistence_error(self,task_id:str,operation:str,details:Any)->None:
        logger.exception("task_state_persistence_error task_id=%s operation=%s details=%r",task_id,operation,details)
        message=f"{_now()} task_state_persistence_error operation={operation} details={details!r}\n"
        try:
            with (self.task_dir(task_id)/"task-persistence-errors.log").open("a",encoding="utf-8") as stream: stream.write(message)
        except OSError: logger.exception("Unable to write task persistence fallback log task_id=%s",task_id)
        try:
            run_log=str(self.state(task_id).get("log_path") or "")
            if run_log:
                with Path(run_log).open("a",encoding="utf-8") as stream: stream.write(message)
        except OSError: logger.exception("Unable to append task persistence error to run.log task_id=%s",task_id)
    def events_after(self,task_id:str,sequence:int)->list[dict[str,Any]]:
        path=self.task_dir(task_id)/"events.jsonl"; result=[]
        if not path.exists(): return result
        try:
            with path.open("r",encoding="utf-8") as stream: lines=stream.read().splitlines()
        except (FileNotFoundError,PermissionError,OSError): return []
        for line in lines:
            event=json.loads(line)
            if int(event["sequence"])>sequence: result.append(event)
        return result
    def result(self,task_id:str)->dict[str,Any]:
        path=self.task_dir(task_id)/"result.json"
        try:
            with path.open("r",encoding="utf-8") as stream: raw=stream.read()
            return json.loads(raw)
        except (FileNotFoundError,PermissionError,json.JSONDecodeError): return {}
    def latest_for_project(self,project_id:str)->str:
        rows=[]
        for path in self.root.glob("*/state.json"):
            state=self.state(path.parent.name)
            if state.get("project_id")==project_id: rows.append((state.get("created_at",""),state.get("task_id","")))
        return max(rows,default=("",""))[1]
    def recover_interrupted(self)->None:
        for path in self.root.glob("*/state.json"):
            state=self.state(path.parent.name)
            if state.get("status") in ACTIVE and state.get("task_id") not in _events:
                task_id=state["task_id"]; self.update(task_id,status="failed",ended_at=_now(),failure_reason="应用重启中断后台任务",termination_reason="application_restarted")
                self.emit(task_id,"error","应用重启，原后台任务已标记失败",termination_reason="application_restarted")

def submit_generation(store:GenerationTaskStore,fn:Callable[[Callable[[],bool],Callable[...,dict[str,Any]]],Any],*,project_id:str,requirement_id:str,model:str,mode:str,total:int)->str:
    task_id="RUN-"+uuid.uuid4().hex[:12].upper(); directory=store.task_dir(task_id); directory.mkdir(parents=True,exist_ok=False)
    state={"task_id":task_id,"run_id":task_id,"project_id":project_id,"requirement_id":requirement_id,"model":model,"requested_mode":mode,"actual_mode":"","status":"created","created_at":_now(),"started_at":"","ended_at":"","failure_reason":"","termination_reason":"","generated_case_count":0,"persisted_case_count":0,"completed_requirements":0,"result_reference":"","last_event_sequence":0,"current_stage":"等待启动","total":total,"log_path":"","agent_to_direct":False}
    _atomic(directory/"state.json",state); cancel=threading.Event(); _events[task_id]=cancel
    def work():
        store.safe_update(task_id,status="running",started_at=_now()); store.safe_emit(task_id,"status","正在构建GenerationPackage")
        try:
            result=fn(cancel.is_set,lambda kind,content,**extra:store.safe_emit(task_id,kind,content,**extra))
            generated=len(result.get("cases") or []); failed=result.get("failed") or []; cancelled=bool(result.get("cancelled") or cancel.is_set())
            status="cancelled" if cancelled else ("failed" if failed and not generated else "completed")
            modes=[x.get("generation_mode") for x in result.get("diagnostic_runs") or [] if x.get("generation_mode")]
            diagnostics=result.get("diagnostic_runs") or []; agent_to_direct=any(x.get("agent_failure") for x in diagnostics)
            actual="Agent→Direct" if agent_to_direct else (" / ".join(dict.fromkeys(modes)) or mode)
            log_path=next((str(x.get("diagnostic_log_path") or "") for x in diagnostics if x.get("diagnostic_log_path")),"")
            if log_path: store.safe_update(task_id,log_path=log_path)
            try: _atomic(directory/"result.json",result)
            except Exception: store._record_persistence_error(task_id,"result write",{"case_count":generated})
            reason="client_cancelled" if cancelled else (str(failed[0].get("error")) if failed else "")
            process_log_incomplete=(directory/"task-persistence-errors.log").exists()
            store.safe_update(task_id,status=status,ended_at=_now(),actual_mode=actual,failure_reason=reason,termination_reason=reason if cancelled else "",generated_case_count=generated,persisted_case_count=generated,result_reference=str(directory/"result.json"),log_path=log_path,agent_to_direct=agent_to_direct,process_log_incomplete=process_log_incomplete,completed_requirements=len(result.get("completed") or [])+len(result.get("skipped") or []))
            store.safe_emit(task_id,"done","生成已停止" if cancelled else ("生成失败" if status=="failed" else "生成完成"),status=status)
            return result
        except Exception as exc:
            reason=str(exc); termination="repetition_detected" if "repetition_detected" in reason else ("client_cancelled" if cancel.is_set() else "")
            status="cancelled" if cancel.is_set() else "failed"; store.safe_update(task_id,status=status,ended_at=_now(),failure_reason=f"model_error: {type(exc).__name__}: {exc}",termination_reason=termination)
            store.safe_emit(task_id,"error",f"model_error: {type(exc).__name__}: {exc}",termination_reason=termination); store.safe_emit(task_id,"done","生成已停止" if status=="cancelled" else "生成失败",status=status); raise
        finally:
            current=store.state(task_id)
            if current.get("status") not in TERMINAL:
                fallback="cancelled" if cancel.is_set() else "failed"; store.safe_update(task_id,status=fallback,ended_at=_now(),termination_reason="client_cancelled" if cancel.is_set() else "missing_terminal_state",failure_reason=current.get("failure_reason") or "task lifecycle ended without terminal state")
            _events.pop(task_id,None)
    future=_executor.submit(work); _futures[task_id]=future; return task_id

def request_cancel(store:GenerationTaskStore,task_id:str)->bool:
    state=store.state(task_id)
    if state.get("status") not in {"created","running"}: return False
    store.safe_update(task_id,status="cancel_requested",termination_reason="client_cancelled"); store.safe_emit(task_id,"status","正在停止本次生成")
    event=_events.get(task_id)
    if event: event.set(); return True
    store.safe_update(task_id,status="cancelled",ended_at=_now()); store.safe_emit(task_id,"done","生成已停止",status="cancelled"); return True
