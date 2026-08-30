"""Runtime gateway connecting sessions, attachments, tools, Ollama and outputs."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from infrastructure.assistant_exporters import AssistantExporter
from infrastructure.documents.assistant_ingestor import AssistantIngestor, safe_filename

from .intent_router import page_url, route_intent
from .session_store import SessionStore
from .tool_registry import EmptyArgs, ToolRegistry
from .tool_result import ToolResult
from .traceability import extract_traceability_items, matrix_docx_content, validate_traceability_matrix

logger = logging.getLogger("test_agent.assistant")


@dataclass(frozen=True)
class AssistantResponse:
    message: str; status: str = "success"; tool_runs: list[dict] = field(default_factory=list)
    source_attachments: list[dict] = field(default_factory=list); output_files: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    def to_dict(self)->dict: return asdict(self)


class PageArgs(BaseModel):
    model_config=ConfigDict(extra="forbid"); page:str; base_url:str="http://127.0.0.1:8501"
class ProjectArgs(BaseModel):
    model_config=ConfigDict(extra="forbid"); project_id:str=""
class RequirementArgs(ProjectArgs): requirement_id:str
class CaseArgs(ProjectArgs): case_id:str; feedback:str=""
class AttachmentArgs(BaseModel):
    model_config=ConfigDict(extra="forbid"); session_id:str; attachment_id:str
class TextExportArgs(BaseModel):
    model_config=ConfigDict(extra="forbid"); session_id:str; filename:str; content:str=Field(min_length=1); sources:list[str]=[]
class DocxExportArgs(TextExportArgs): content:str|dict
class XlsxExportArgs(BaseModel):
    model_config=ConfigDict(extra="forbid"); session_id:str; filename:str; sheets:list[dict]=Field(min_length=1); sources:list[str]=[]
class OpenPathArgs(BaseModel):
    model_config=ConfigDict(extra="forbid"); path:str


class AssistantGateway:
    def __init__(self, ui_service, settings, project_root:Path, chat_handler:Callable[...,str]|None=None)->None:
        self.service=ui_service; self.settings=settings; self.project_root=project_root.resolve(); self.current_project_id=""; self.chat_handler=chat_handler
        self.output_root=(self.project_root/settings.xiaoche_output_dir).resolve()
        self.ingestor=AssistantIngestor(self.output_root,max_bytes=settings.xiaoche_max_attachment_bytes,max_chars=min(settings.document_max_chars,settings.xiaoche_session_context_budget*8),pdf_max_pages=settings.pdf_max_pages)
        self.exporter=AssistantExporter(self.output_root); self.sessions=SessionStore(ui_service.manager)
        self.registry=ToolRegistry(self.project_root/"logs"/"assistant"/"tool-audit.jsonl"); self._register_tools()

    def _register_tools(self)->None:
        r=self.registry.register
        r("list_projects",EmptyArgs,lambda:self.service.list_projects()); r("get_project_status",ProjectArgs,self.get_project_status)
        r("open_streamlit_page",PageArgs,lambda page,base_url="http://127.0.0.1:8501":{"page":page,"url":page_url(base_url,page)})
        r("list_requirements",ProjectArgs,lambda project_id="":self.service.list_requirements(self._project(project_id)))
        r("get_requirement",RequirementArgs,lambda requirement_id,project_id="":self.service.get_requirement(self._project(project_id),requirement_id))
        r("list_generated_cases",ProjectArgs,lambda project_id="":self.service.list_generated_cases(self._project(project_id)))
        r("get_case_review_summary",ProjectArgs,self.get_case_review_summary)
        r("regenerate_single_case",CaseArgs,lambda case_id,feedback="",project_id="":self.service.regenerate_single_case(self._project(project_id),case_id,feedback))
        r("export_project_excel",ProjectArgs,lambda project_id="":{"path":str(self.service.export_excel(self._project(project_id)).resolve())})
        r("parse_document",AttachmentArgs,self.parse_attachment); r("extract_traceability_items",AttachmentArgs,self.extract_attachment_traceability)
        r("create_docx",DocxExportArgs,lambda **kwargs:self.exporter.create_docx(**kwargs).to_dict())
        r("create_xlsx",XlsxExportArgs,lambda **kwargs:self.exporter.create_xlsx(**kwargs).to_dict())
        r("create_pdf",TextExportArgs,lambda **kwargs:self.exporter.create_pdf(**kwargs).to_dict())
        r("create_txt",TextExportArgs,lambda **kwargs:self.exporter.create_txt(**kwargs).to_dict())
        r("create_markdown",TextExportArgs,lambda **kwargs:self.exporter.create_markdown(**kwargs).to_dict())
        r("open_output_file",OpenPathArgs,lambda path:self.open_output_path(path,False)); r("reveal_output_directory",OpenPathArgs,lambda path:self.open_output_path(path,True))
        r("cancel_current_task",EmptyArgs,lambda:ToolResult(False,"当前任务不支持安全取消，未伪造取消成功。",error="cancellation_not_supported"))

    def upload_attachment(self,session_id:str,source_path:Path)->dict:
        logger.info("assistant_attachment_selected session=%s name=%s",session_id,source_path.name)
        staged=self.ingestor.stage(source_path,session_id); relative=Path(staged.stored_path).resolve().relative_to(self.output_root).as_posix()
        data={"attachment_id":staged.attachment_id,"session_id":session_id,"message_id":None,"original_name":staged.name,"safe_name":Path(staged.stored_path).name,"media_type":staged.metadata["media_type"],"extension":"."+staged.kind,"size_bytes":staged.size,"sha256":staged.sha256,"stored_path":relative,"parse_status":"pending","parse_error":"","parser_name":"","text_chars":0,"paragraph_count":0,"table_count":0,"sheet_count":0,"page_count":0,"summary_json":{},"parsed_json":{}}
        self.sessions.create_attachment(data); logger.info("assistant_attachment_staged session=%s id=%s name=%s type=%s bytes=%s",session_id,staged.attachment_id,staged.name,staged.kind,staged.size)
        return self.sessions.get_attachment(session_id,staged.attachment_id)

    def parse_attachment(self,session_id:str,attachment_id:str)->dict:
        row=self.sessions.get_attachment(session_id,attachment_id)
        if row["parse_status"]=="success" and row.get("parsed_json"): return row["parsed_json"]
        self.sessions.update_attachment_parse(attachment_id,"parsing",parse_error=""); logger.info("assistant_parse_started session=%s id=%s name=%s",session_id,attachment_id,row["original_name"])
        try:
            path=self._attachment_path(row["stored_path"]); parsed=self.ingestor.parse_stored(path,attachment_id,row["original_name"]); meta=parsed.metadata
            payload={"attachment_id":attachment_id,"name":parsed.name,"kind":parsed.kind,"size":parsed.size,"text":parsed.text,"metadata":meta,"sha256":parsed.sha256}
            summary={"title":meta.get("title", ""),"text_preview":parsed.text[:500],"truncated":meta.get("truncated",False)}
            self.sessions.update_attachment_parse(attachment_id,"success",parser_name=meta.get("parser_name",""),text_chars=len(parsed.text),paragraph_count=meta.get("paragraph_count",0),table_count=meta.get("table_count",0),sheet_count=meta.get("sheet_count",0),page_count=meta.get("page_count",0),summary_json=summary,parsed_json=payload)
            logger.info("assistant_parse_completed session=%s id=%s name=%s chars=%s paragraphs=%s tables=%s sheets=%s pages=%s truncated=%s",session_id,attachment_id,parsed.name,len(parsed.text),meta.get("paragraph_count",0),meta.get("table_count",0),meta.get("sheet_count",0),meta.get("page_count",0),meta.get("truncated",False)); return payload
        except Exception as exc:
            self.sessions.update_attachment_parse(attachment_id,"failed",parse_error=f"{type(exc).__name__}: {exc}"); logger.exception("assistant_parse_failed session=%s id=%s name=%s",session_id,attachment_id,row["original_name"]); raise

    def handle_message(self,session_id:str,project_id:str,text:str,attachment_ids:list[str]|None=None,cancel_token:Any=None)->AssistantResponse:
        ids=list(dict.fromkeys(attachment_ids or [])); message_id=self.sessions.add_message(session_id,"user",text,ids); logger.info("assistant_message_received session=%s message=%s attachment_ids=%s",session_id,message_id,ids)
        try:
            if ids: return self._handle_attachments(session_id,message_id,text,ids)
            export_tool=self._requested_export_tool(text)
            if export_tool: return self._handle_context_export(session_id,message_id,text,export_tool)
            intent=route_intent(text,self.settings.xiaoche_app_url); logger.info("assistant_intent session=%s message=%s tool=%s attachment_count=0",session_id,message_id,intent.tool or "chat")
            if intent.tool:
                result=self._run_tool(session_id,message_id,intent.tool,intent.arguments or {})
                response=AssistantResponse(result.message if not result.success else self._format_tool_result(intent.tool,result.data),"success" if result.success else "error",[{"tool":intent.tool,"success":result.success}])
            else: response=self._ordinary_chat(session_id,text)
            self.sessions.add_message(session_id,"assistant",response.message,metadata=response.to_dict()); return response
        except Exception as exc:
            names=[]
            for attachment_id in ids:
                try: names.append(self.sessions.get_attachment(session_id,attachment_id)["original_name"])
                except ValueError: names.append(attachment_id)
            logger.exception("assistant_message_failed session=%s message=%s attachments=%s",session_id,message_id,names); response=AssistantResponse(f"失败阶段：附件解析或工具执行\n附件名称：{', '.join(names) or '无'}\n错误类型：{type(exc).__name__}\n建议：确认文件未损坏并查看日志后重试；原始附件未被修改。\n日志入口：logs/assistant/","error",warnings=[str(exc)]); self.sessions.add_message(session_id,"assistant",response.message,metadata=response.to_dict()); return response

    def _handle_attachments(self,session_id:str,message_id:int,text:str,ids:list[str])->AssistantResponse:
        normalized="".join(text.lower().split()); parsed=[]; runs=[]
        for attachment_id in ids:
            result=self._run_tool(session_id,message_id,"parse_document",{"session_id":session_id,"attachment_id":attachment_id}); runs.append({"tool":"parse_document","attachment_id":attachment_id,"success":result.success})
            if not result.success: raise RuntimeError(result.error or result.message)
            parsed.append(result.data["result"])
        logger.info("assistant_attachment_context session=%s message=%s attachments=%s context_chars=%s",session_id,message_id,ids,sum(len(item.get("text", "")) for item in parsed))
        if any(token in normalized for token in ("追踪矩阵","追踪表","追溯矩阵","需求矩阵")):
            items=[]
            for item in parsed: items.extend(extract_traceability_items(item))
            validate_traceability_matrix(items); content=matrix_docx_content("需求追踪矩阵",items)
            output=self._create_and_record(session_id,message_id,ids,"create_docx",{"session_id":session_id,"filename":"需求追踪矩阵.docx","content":content,"sources":ids}); runs.extend(output[1])
            pending=sum(1 for item in items if item.note or item.coverage_status=="待人工确认")
            message=f"已生成并回读验证 Word 需求追踪矩阵。\n文件：{output[0]['filename']}\n记录数：{len(items)}\n待确认：{pending}"
            response=AssistantResponse(message,"success",runs,[self._source_card(x) for x in parsed],[output[0]])
        elif any(token in normalized for token in ("转换成word","转化成word","输出word","生成word","生成docx")):
            body="\n\n".join(item["text"] for item in parsed); output=self._create_and_record(session_id,message_id,ids,"create_docx",{"session_id":session_id,"filename":"附件整理.docx","content":{"title":"附件整理","paragraphs":[body]},"sources":ids}); runs.extend(output[1]); response=AssistantResponse(f"已生成并回读验证 Word 文件：{output[0]['filename']}","success",runs,[self._source_card(x) for x in parsed],[output[0]])
        elif len(parsed)>1 and any(token in normalized for token in ("对比","比较","差异")):
            left=set(parsed[0]["text"].splitlines()); right=set(parsed[1]["text"].splitlines()); message=f"已解析并比较附件。仅第一个附件包含 {len(left-right)} 行，仅第二个附件包含 {len(right-left)} 行，共同内容 {len(left&right)} 行。"; response=AssistantResponse(message,"success",runs,[self._source_card(x) for x in parsed])
        else:
            cards=[self._source_card(item) for item in parsed]; details=[]
            for card in cards: details.append(f"{card['name']}：{card['kind'].upper()}，标题“{card['title'] or '未设置'}”，{card['paragraph_count']} 个段落、{card['table_count']} 个表格、{card['sheet_count']} 个工作表、{card['page_count']} 页。内容概览：{card['preview']}")
            response=AssistantResponse("\n".join(details),"success",runs,cards)
        self.sessions.add_message(session_id,"assistant",response.message,metadata=response.to_dict()); return response

    def _handle_context_export(self,session_id:str,message_id:int,text:str,tool:str)->AssistantResponse:
        rows=self.sessions.page(session_id,30,before_id=message_id); source=next((row["content"] for row in reversed(rows) if row["role"] in {"assistant","tool"} and row["content"].strip()),"")
        if not source: response=AssistantResponse("当前会话没有可导出的有效内容，请先解析附件或提供正文。","error",warnings=["missing_context"]); self.sessions.add_message(session_id,"assistant",response.message,metadata=response.to_dict()); return response
        filenames={"create_docx":"会话内容.docx","create_xlsx":"会话内容.xlsx","create_pdf":"会话内容.pdf","create_txt":"会话内容.txt","create_markdown":"会话内容.md"}
        if tool=="create_xlsx": args={"session_id":session_id,"filename":filenames[tool],"sheets":[{"name":"会话内容","rows":[["内容"],*[[line] for line in source.splitlines() if line.strip()]]}],"sources":[]}
        elif tool=="create_docx": args={"session_id":session_id,"filename":filenames[tool],"content":{"title":"会话内容","paragraphs":[source]},"sources":[]}
        else: args={"session_id":session_id,"filename":filenames[tool],"content":source,"sources":[]}
        output,runs=self._create_and_record(session_id,message_id,[],tool,args); response=AssistantResponse(f"已生成并回读验证文件：{output['filename']}","success",runs,output_files=[output]); self.sessions.add_message(session_id,"assistant",response.message,metadata=response.to_dict()); return response

    def _ordinary_chat(self,session_id:str,text:str)->AssistantResponse:
        if self.chat_handler is None:
            from desktop_pet.ollama_chat import chat
            handler=lambda message,history:chat(self.settings.ollama_base_url,self.settings.ollama_model,message,self.settings.xiaoche_chat_timeout,history)
        else: handler=self.chat_handler
        history=self.sessions.context(session_id,self.settings.xiaoche_session_context_budget); reply=handler(text,history)
        logger.info("assistant_ollama_request session=%s attachment_context=false history_messages=%s",session_id,len(history)); return AssistantResponse(reply)

    def _run_tool(self,session_id:str,message_id:int,name:str,args:dict)->ToolResult:
        result=self.registry.execute(session_id,name,args); run_id=self.sessions.add_tool_run(session_id,message_id,name,self._safe_args(args),"success" if result.success else "error",self._safe_result(name,result.data),result.error); logger.info("assistant_tool_run session=%s message=%s run=%s tool=%s success=%s",session_id,message_id,run_id,name,result.success); return result

    def _create_and_record(self,session_id:str,message_id:int,ids:list[str],name:str,args:dict)->tuple[dict,list[dict]]:
        result=self._run_tool(session_id,message_id,name,args)
        if not result.success: raise RuntimeError(result.error or result.message)
        output=result.data["result"]; runs=self.sessions.manager.connections
        with runs.connection() as conn: row=conn.execute("SELECT MAX(tool_run_id) FROM assistant_tool_runs WHERE session_id=? AND message_id=? AND tool_name=?",(session_id,message_id,name)).fetchone(); tool_run_id=int(row[0])
        self.sessions.add_output(session_id,message_id,ids,tool_run_id,output); logger.info("assistant_output_validated session=%s message=%s tool=%s name=%s bytes=%s validation=%s",session_id,message_id,name,output["filename"],output["size"],output["validation"]); return output,[{"tool":name,"success":True,"tool_run_id":tool_run_id}]

    def extract_attachment_traceability(self,session_id:str,attachment_id:str)->dict:
        parsed=self.parse_attachment(session_id,attachment_id); items=extract_traceability_items(parsed); validate_traceability_matrix(items); return {"items":[item.to_dict() for item in items],"count":len(items)}
    def _attachment_path(self,relative:str)->Path:
        if relative.startswith(("\\","//")) or ".." in Path(relative).parts: raise ValueError("附件路径非法")
        path=(self.output_root/relative).resolve()
        if self.output_root not in path.parents: raise ValueError("附件路径越界")
        return path
    @staticmethod
    def _source_card(item:dict)->dict:
        meta=item.get("metadata") or {}; return {"attachment_id":item["attachment_id"],"name":item["name"],"kind":item["kind"],"title":meta.get("title",""),"paragraph_count":meta.get("paragraph_count",0),"table_count":meta.get("table_count",0),"sheet_count":meta.get("sheet_count",0),"page_count":meta.get("page_count",0),"preview":item.get("text","")[:240],"truncated":meta.get("truncated",False)}
    @staticmethod
    def _safe_args(args:dict)->dict: return {key:(f"<{len(value)} chars>" if key=="content" and isinstance(value,str) else "<structured content>" if key=="content" else value) for key,value in args.items()}
    @staticmethod
    def _safe_result(name:str,data:dict)->dict:
        value=data.get("result") if isinstance(data,dict) else None
        if name=="parse_document" and isinstance(value,dict):
            meta=value.get("metadata") or {}
            return {"result":{"attachment_id":value.get("attachment_id"),"name":value.get("name"),"kind":value.get("kind"),"text_chars":len(value.get("text") or ""),"paragraph_count":meta.get("paragraph_count",0),"table_count":meta.get("table_count",0),"sheet_count":meta.get("sheet_count",0),"page_count":meta.get("page_count",0),"truncated":meta.get("truncated",False)}}
        return data
    @staticmethod
    def _requested_export_tool(text:str)->str:
        value="".join(text.lower().split())
        if not any(token in value for token in ("生成","输出","导出","转换","转化")): return ""
        if "word" in value or "docx" in value: return "create_docx"
        if "excel" in value or "xlsx" in value: return "create_xlsx"
        if "pdf" in value: return "create_pdf"
        if "markdown" in value or "md" in value: return "create_markdown"
        if "txt" in value or "文本" in value: return "create_txt"
        return ""
    @staticmethod
    def _format_tool_result(name:str,data:dict)->str: return f"工具 {name} 已成功执行。"
    def select_project(self,project_id:str)->None:
        if project_id and not self.service.get_project(project_id): raise ValueError("项目不存在")
        self.current_project_id=project_id
    def _project(self,project_id:str="")->str:
        value=project_id or self.current_project_id
        if not value: raise ValueError("请先选择项目")
        return value
    def get_project_status(self,project_id:str="")->dict:
        pid=self._project(project_id); requirements=self.service.list_requirements(pid); cases=self.service.list_generated_cases(pid); covered={str(case.get("requirement_id") or "") for case in cases}; return {"project":self.service.get_project(pid),"requirements":len(requirements),"generated_cases":len(cases),"requirements_without_cases":sum(1 for row in requirements if str(row.get("requirement_id") or "") not in covered),"review":self.get_case_review_summary(pid)}
    def get_case_review_summary(self,project_id:str="")->dict:
        cases=self.service.list_generated_cases(self._project(project_id)); pending=sum(1 for row in cases if (row.get("case_json") or {}).get("need_human_confirm") if isinstance(row.get("case_json") or {},dict)); return {"total":len(cases),"pending_confirmation":pending}
    def open_output_path(self,path:str,reveal:bool)->dict:
        import os
        candidate=Path(path).resolve()
        if candidate!=self.output_root and self.output_root not in candidate.parents: raise ValueError("只允许打开助手输出目录内文件")
        target=candidate.parent if reveal and candidate.is_file() else candidate
        if not target.exists(): raise ValueError("输出路径不存在")
        if not hasattr(os,"startfile"): raise RuntimeError("仅支持 Windows 桌面打开文件")
        os.startfile(str(target)); return {"opened":str(target)}
