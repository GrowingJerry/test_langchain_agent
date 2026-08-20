"""Unified project test-case generation, validation, quality, and persistence service."""

from __future__ import annotations

import inspect
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agents.test_case.agent import TestCaseAgent
from agents.test_case.context import AgentRuntimeContext
from agents.test_case.output_schema import GeneratedCaseBundle, TestCaseAgentRequest
from config.settings import Settings, settings as default_settings
from domain.rules.case_quality import evaluate_case_quality
from application.services.generation_context import ContextBuilder
from infrastructure.database.json_codec import dumps_json
from infrastructure.repositories.base import new_id, now_iso
from domain.exceptions import (
    AgentCallLimitError,
    AgentExecutionError,
    ConfigurationError,
    ModelUnavailableError,
    PersistenceError,
    ProjectScopeError,
    StructuredOutputError,
)
from domain.schemas.test_case import TestCase
from infrastructure.llm.ollama_health import OllamaHealthClient
from application.services.fallback_generation_service import FallbackGenerationService
from application.services.generation_package import build_generation_package
from infrastructure.runtime.generation_run_log import GenerationRunLog
from infrastructure.llm.stream_guard import StreamGuard, GenerationTerminated
from infrastructure.llm.ollama_errors import raise_for_ollama_status, response_error_text
import logging
logger = logging.getLogger("test_agent.generation")
from application.services.case_id_service import CaseIdService


GenerationMode = Literal["agent", "model_direct", "rule_fallback", "manual"]


class _DirectRouteComplete(Exception):
    """Internal control flow: Auto selected Direct without attempting Agent."""


class _DirectRouteFailed(Exception):
    """Internal control flow: Auto Direct failed and must not be retried unchanged."""


class GenerationRequest(BaseModel):
    """Validated input for one project-scoped generation run."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    requirement_ids: List[str] = Field(default_factory=list)
    scenario_ids: List[str] = Field(default_factory=list)
    case_type: str = "功能测试"
    case_count: int = Field(default=1, ge=1, le=100)
    auto_case_count: bool = False
    requested_mode: Literal["auto", "agent", "rule", "manual"] = "auto"
    use_project_kb: bool = True
    use_history: bool = True
    top_k_chunks: int = Field(default=5, ge=1, le=50)
    top_k_history: int = Field(default=5, ge=0, le=50)
    additional_instructions: str = ""
    manual_cases: List[TestCase] = Field(default_factory=list)
    recommended_test_type: str = ""
    selected_test_type: str = ""
    test_type_overridden: bool = False
    test_type_confidence: float = 0.0
    test_type_reasons: List[str] = Field(default_factory=list)
    batch_id: str = ""

    @model_validator(mode="after")
    def _validate_manual_mode(self) -> "GenerationRequest":
        if self.requested_mode == "manual" and not self.manual_cases:
            raise ValueError("manual mode requires manual_cases")
        if self.requested_mode != "manual" and self.manual_cases:
            raise ValueError("manual_cases are only accepted in manual mode")
        if not self.requirement_ids and not self.scenario_ids:
            raise ValueError("requirement_ids or scenario_ids is required")
        if self.auto_case_count and self.case_count < 2:
            raise ValueError("auto case count requires a limit of at least 2")
        return self


class GeneratedCaseRecord(BaseModel):
    """One canonical case with persistence compatibility data and quality result."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    case: TestCase
    persistence_data: Dict[str, Any]
    quality: Dict[str, Any]
    context_id: str = ""


class GenerationResult(BaseModel):
    """Unified non-UI result returned by GenerationService."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    project_id: str
    generation_run_id: str
    generation_mode: GenerationMode
    fallback_reason: str = ""
    cases: List[GeneratedCaseRecord] = Field(default_factory=list)
    overall_missing_information: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    used_tool_names: List[str] = Field(default_factory=list)
    retrieved_source_chunk_ids: List[str] = Field(default_factory=list)
    diagnostic_run_id: str = ""
    diagnostic_log_path: str = ""
    diagnostic_bundle_path: str = ""
    context_fingerprints: List[str] = Field(default_factory=list)
    agent_failure: str = ""
    agent_direct_context_equal: bool | None = None


AgentBuilder = Callable[[AgentRuntimeContext], Any]
DirectModelGenerator = Callable[
    [List[Dict[str, Any]], GenerationRequest, str], List[TestCase]
]


class GenerationService:
    """Single project-scoped generation entry point with explicit fallback semantics."""

    def __init__(
        self,
        manager: Any,
        case_library: Optional[Any] = None,
        settings: Settings = default_settings,
        health_client: Optional[OllamaHealthClient] = None,
        agent_builder: Optional[AgentBuilder] = None,
        direct_model_generator: Optional[DirectModelGenerator] = None,
        fallback_service: Optional[FallbackGenerationService] = None,
    ) -> None:
        self.manager = manager
        self.case_library = case_library
        self.settings = settings
        self.health_client = health_client or OllamaHealthClient(settings)
        self.agent_builder = agent_builder or (lambda runtime: TestCaseAgent(runtime))
        self.direct_model_generator = direct_model_generator
        self.fallback_service = fallback_service or FallbackGenerationService()
        self.context_builder = ContextBuilder(manager, case_library)

    def generate_test_cases(
        self,
        request: GenerationRequest,
        progress_callback: Callable[[dict[str, str]], None] | None = None,
        cancellation_callback: Callable[[], bool] | None = None,
    ) -> GenerationResult:
        """Validate, generate, score, persist, and return one explicit-mode result."""
        self._validate_scope(request)
        if len(request.requirement_ids) > 1:
            raise ValueError("每次模型调用只能整批生成一个最低功能需求；请使用需求批处理入口")
        requirement = self.manager.get_requirement(request.project_id, request.requirement_ids[0]) if request.requirement_ids else {}
        project = self.manager.get_project(request.project_id) or {}
        run_log = GenerationRunLog(Path(__file__).resolve().parents[2], request.project_id,
            settings=self.settings, operation_type="test_case_generation", operation_name="测试用例生成",
            project_name=str(project.get("name") or project.get("project_name") or ""),
            requirement_id=request.requirement_ids[0] if request.requirement_ids else "",
            requirement_name=str((requirement or {}).get("title") or ""), test_type=request.case_type,
            generation_mode=request.requested_mode, model=self.settings.test_case_model)
        started = time.monotonic()
        self._emit(progress_callback, "status", "正在构建GenerationPackage")
        contexts = self._build_contexts(request)
        packages = [
            build_generation_package(
                self.manager,
                context,
                num_ctx=self.settings.ollama_num_ctx,
                num_predict=self.settings.ollama_structured_num_predict,
                case_count=request.case_count,
                case_type=request.case_type,
                settings=self.settings,
            )
            for context in contexts
        ]
        fingerprints = [item["fingerprint"] for item in packages]
        run_log.artifact("01-input-summary.json", request.model_dump(mode="json"))
        run_log.artifact("02-normalized-context.json", packages)
        run_log.event(
            "需求生成",
            "构建统一 GenerationPackage 完成",
            model=self.settings.test_case_model,
            fingerprints=fingerprints,
            estimated_tokens=[item["token_estimate_after"] for item in packages],
            num_ctx=self.settings.ollama_num_ctx,
            num_predict=self.settings.ollama_structured_num_predict,
            trimmed_fields=[item["trimmed_fields"] for item in packages],
            capacity=[{key:item[key] for key in ("input_tokens","expected_output_tokens","available_output_tokens","case_count")} for item in packages],
        )
        self._emit(
            progress_callback,
            "status",
            f"模型 {self.settings.test_case_model}；输入约 "
            f"{sum(item['token_estimate_after'] for item in packages)} tokens；"
            f"num_ctx={self.settings.ollama_num_ctx}；"
            f"num_predict={self.settings.ollama_structured_num_predict}；"
            f"日志 {run_log.log_path}",
        )
        if any(item["may_exceed_context"] for item in packages):
            run_log.finish("failed", error="context_window_exceeded", fingerprints=fingerprints)
            raise StructuredOutputError("GenerationPackage 在证据优先压缩后仍可能超过配置上下文")
        preflight_fallback_reason = "" if request.requested_mode == "manual" else self._preflight_fallback_reason(request)
        insufficient = next((item for item in packages if not item["capacity_sufficient"]), None)
        if insufficient and not preflight_fallback_reason and request.requested_mode != "manual":
            run_log.finish("failed", error="generation_capacity_insufficient", capacity={key:insufficient[key] for key in ("input_tokens","expected_output_tokens","available_output_tokens","case_count")})
            raise StructuredOutputError(
                "生成容量预检失败：generation_capacity_insufficient；"
                f"input_tokens={insufficient['input_tokens']}，expected_output_tokens={insufficient['expected_output_tokens']}，"
                f"available_output_tokens={insufficient['available_output_tokens']}，case_count={insufficient['case_count']}。"
                "请减少用例数或调整 num_ctx/num_predict。"
            )
        mode: GenerationMode
        fallback_reason = ""
        warnings: List[str] = []
        used_tool_names: List[str] = []
        retrieved_chunk_ids: List[str] = []
        overall_missing: List[str] = []
        agent_failure = ""
        agent_direct_context_equal: bool | None = None

        if request.requested_mode == "manual":
            mode = "manual"
            canonical_cases = [
                case.model_copy(update={"generation_mode": "manual"})
                for case in request.manual_cases
            ]
        else:
            fallback_reason = preflight_fallback_reason
            if fallback_reason:
                mode = "rule_fallback"
                canonical_cases = self.fallback_service.generate(
                    contexts, request.case_count, request.case_type, fallback_reason
                )
            else:
                prefer_direct = request.requested_mode == "auto" and self._generation_package_is_complete(packages)
                try:
                    if prefer_direct:
                        self._emit(progress_callback, "status", "Auto路由：GenerationPackage已完整，优先使用Direct。")
                        try:
                            canonical_cases = self._generate_with_direct_model(packages, request, "auto_direct_complete_package", progress_callback, run_log, cancellation_callback)
                        except Exception as direct_exc:
                            raise _DirectRouteFailed() from direct_exc
                        mode = "model_direct"
                        agent_direct_context_equal = True
                        raise _DirectRouteComplete()
                    runtime = AgentRuntimeContext(
                        project_id=request.project_id,
                        manager=self.manager,
                        case_library=self.case_library if request.use_history else None,
                        settings=self.settings,
                    )
                    agent = self.agent_builder(runtime)
                    agent_request = TestCaseAgentRequest(
                            requirement_ids=request.requirement_ids,
                            scenario_ids=request.scenario_ids,
                            case_count=request.case_count,
                            case_type=request.case_type,
                            additional_instructions=request.additional_instructions,
                            auto_case_count=request.auto_case_count,
                        )
                    generate_parameters = inspect.signature(agent.generate).parameters
                    agent_kwargs: Dict[str, Any] = {}
                    if "progress_callback" in generate_parameters:
                        agent_kwargs["progress_callback"] = progress_callback
                    if "generation_package" in generate_parameters:
                        agent_kwargs["generation_package"] = packages[0]["package"]
                    if "request_run_id" in generate_parameters:
                        agent_kwargs["request_run_id"] = run_log.run_id
                    schema = GeneratedCaseBundle.model_json_schema()
                    run_log.artifact("03-model-request.json", {"mode": "agent", "fingerprints": fingerprints, "packages": packages,
                        "schema_summary":{"title":schema.get("title"),"top_level_keys":sorted(schema),"definitions":len(schema.get("$defs") or {}),"schema_chars":len(json.dumps(schema,ensure_ascii=False)),"requested_at":datetime.now(timezone.utc).isoformat()}})
                    run_log.event("需求生成", "调用 Agent：开始", mode="agent", fingerprint=fingerprints[0] if fingerprints else "")
                    self._emit(progress_callback,"status","正在调用Agent")
                    bundle = agent.generate(agent_request, **agent_kwargs)
                    bundle = (
                        bundle
                        if isinstance(bundle, GeneratedCaseBundle)
                        else GeneratedCaseBundle.model_validate(bundle)
                    )
                    canonical_cases = bundle.cases
                    mode = "agent"
                    used_tool_names = bundle.used_tool_names
                    retrieved_chunk_ids = bundle.retrieved_source_chunk_ids
                    overall_missing = bundle.overall_missing_information
                    warnings.extend(bundle.warnings)
                    self._validate_agent_cases(canonical_cases, request)
                    run_log.artifact("04-model-raw-response.json", bundle.model_dump(mode="json"))
                    run_log.event("需求生成", "调用 Agent：完成", case_count=len(canonical_cases))
                except _DirectRouteComplete:
                    pass
                except _DirectRouteFailed as routed:
                    direct_exc = routed.__cause__ or routed
                    run_log.failure("需求生成-Direct", direct_exc)
                    run_log.finish("failed", error=f"{type(direct_exc).__name__}: {direct_exc}", fingerprints=fingerprints)
                    run_log.diagnostic_zip()
                    raise StructuredOutputError(f"Auto Direct失败且相同参数不重试：{direct_exc}") from direct_exc
                except Exception as exc:  # noqa: BLE001 - diagnostics must capture every chain failure.
                    agent_failure = f"{type(exc).__name__}: {exc}"
                    status_match = re.search(r'"status_code"\s*:\s*(\d+)', str(exc))
                    run_log.artifact("03-agent-response-diagnostics.json", {"response_status":int(status_match.group(1)) if status_match else None,
                        "error":agent_failure,"correlation_time_utc":datetime.now(timezone.utc).isoformat(),
                        "docker_log_correlation":"match this UTC window against docker logs --timestamps; absence of POST means failure before Ollama handler"})
                    run_log.failure("需求生成-Agent", exc, fingerprint=fingerprints[0] if fingerprints else "")
                    self._emit(progress_callback, "error", f"Agent链生成失败：{agent_failure}")
                    self._emit(progress_callback, "status", "系统将使用完全相同的 GenerationPackage 切换到 Direct 模式。")
                    try:
                        canonical_cases = self._generate_with_direct_model(
                            packages, request, agent_failure, progress_callback, run_log,
                            cancellation_callback,
                        )
                        direct_fingerprints = [item["fingerprint"] for item in packages]
                        agent_direct_context_equal = fingerprints == direct_fingerprints
                        if not agent_direct_context_equal:
                            raise StructuredOutputError("Agent 与 Direct 上下文指纹不一致")
                        mode = "model_direct"
                        fallback_reason = ""
                        warnings.append(
                            f"LangChain Agent 失败，已改用同一 Ollama 模型直接生成：{agent_failure}"
                        )
                        run_log.event("需求生成", "Direct 完成", fingerprint_equal=True, case_count=len(canonical_cases))
                    except Exception as direct_exc:
                        run_log.failure("需求生成-Direct", direct_exc)
                        run_log.finish(
                            "failed",
                            error=f"{type(direct_exc).__name__}: {direct_exc}",
                            agent_failure=agent_failure,
                            fingerprints=fingerprints,
                        )
                        run_log.diagnostic_zip()
                        raise StructuredOutputError(
                            f"Agent 与 Direct 均失败，已停止且未保存空结果：{agent_failure}; "
                            f"direct={type(direct_exc).__name__}: {direct_exc}"
                        ) from direct_exc

        if not canonical_cases:
            raise StructuredOutputError("Generation produced no test cases")
        if len(canonical_cases) > min(request.case_count, self.settings.generation_max_cases_per_requirement):
            raise StructuredOutputError("模型返回用例数超过本次请求或 GENERATION_MAX_CASES_PER_REQUIREMENT")
        for case in canonical_cases:
            payload = case.model_dump(mode="json")
            for field_name, value in payload.items():
                if isinstance(value, str) and len(value) > self.settings.generation_max_field_chars:
                    raise StructuredOutputError(f"用例字段 {field_name} 超过 GENERATION_MAX_FIELD_CHARS")
            if any(len(step) > self.settings.generation_max_step_chars for step in case.test_steps):
                raise StructuredOutputError("用例步骤超过 GENERATION_MAX_STEP_CHARS")
        if mode in ("agent", "model_direct"):
            self._emit(progress_callback,"status","正在执行结构校验")
            canonical_cases = self._validate_and_render_detailed_cases(canonical_cases, packages, request.case_type)
        if cancellation_callback and cancellation_callback():
            run_log.finish("cancelled", failure_reason="client_cancelled")
            raise StructuredOutputError("生成已由用户取消；不保存未完成用例")
        requirement_id = request.requirement_ids[0] if request.requirement_ids else ""
        canonical_cases = CaseIdService(self.manager, self.settings).assign(
            request.project_id, requirement_id, canonical_cases
        )
        metadata = self._generation_metadata(request)
        self._emit(progress_callback, "status", "正在持久化正式测试用例")
        run_id = self.manager.create_generation_run(
            project_id=request.project_id,
            run_type=f"test_case_{mode}",
            model_name=self.settings.ollama_model if mode == "agent" else "",
            prompt_snapshot=json.dumps(
                request.model_dump(mode="json"), ensure_ascii=False
            )[:12000],
            status="completed",
            metadata=metadata,
        )
        self._record_test_type_feedback(request)
        records = self._score_and_persist(
            request, contexts, canonical_cases, run_id, mode, fallback_reason, metadata
        )
        self._emit(progress_callback,"status",f"生成完成，共持久化 {len(records)} 条用例")
        run_log.artifact("05-parsed-output.json", [record.persistence_data for record in records])
        run_log.artifact("06-validation-result.json", [record.quality for record in records])
        run_log.artifact("07-persistence-result.json", {"case_count": len(records), "generation_run_id": run_id})
        run_log.finish("completed", generation_mode=mode, case_count=len(records),
                       case_ids=[record.case.case_id for record in records],
                       elapsed_seconds=round(time.monotonic()-started, 3), fingerprints=fingerprints)
        diagnostic_bundle = run_log.diagnostic_zip()
        if not overall_missing:
            overall_missing = list(
                dict.fromkeys(
                    item
                    for record in records
                    for item in record.case.missing_information
                )
            )
        return GenerationResult(
            project_id=request.project_id,
            generation_run_id=run_id,
            generation_mode=mode,
            fallback_reason=fallback_reason,
            cases=records,
            overall_missing_information=overall_missing,
            warnings=warnings,
            used_tool_names=used_tool_names,
            retrieved_source_chunk_ids=retrieved_chunk_ids,
            diagnostic_run_id=run_log.run_id,
            diagnostic_log_path=str(run_log.log_path),
            diagnostic_bundle_path=str(diagnostic_bundle),
            context_fingerprints=fingerprints,
            agent_failure=agent_failure,
            agent_direct_context_equal=agent_direct_context_equal,
        )

    def _validate_scope(self, request: GenerationRequest) -> None:
        if not self.manager.get_project(request.project_id):
            raise ProjectScopeError(f"Project {request.project_id!r} does not exist")
        for requirement_id in request.requirement_ids:
            if not self.manager.get_requirement(request.project_id, requirement_id):
                raise ProjectScopeError(
                    f"Requirement {requirement_id!r} does not belong to project {request.project_id!r}"
                )
        if request.scenario_ids:
            valid = {
                str(row.get("scenario_id") or "")
                for row in self.manager.list_scenario_cards(request.project_id)
            }
            invalid = [item for item in request.scenario_ids if item not in valid]
            if invalid:
                raise ProjectScopeError(
                    f"Scenarios are outside the bound project: {invalid}"
                )

    def _build_contexts(self, request: GenerationRequest) -> List[Dict[str, Any]]:
        contexts = [
            self.context_builder.build(
                request.project_id,
                requirement_id,
                request.case_type,
                request.top_k_chunks,
                request.top_k_history,
                request.use_project_kb,
                request.use_history,
                persist=True,
            )
            for requirement_id in request.requirement_ids
        ]
        if contexts:
            return contexts
        cards = {
            str(row.get("scenario_id") or ""): row
            for row in self.manager.list_scenario_cards(request.project_id)
        }
        chunks = self.manager.list_chunks(request.project_id, 5000)
        result = []
        for scenario_id in request.scenario_ids:
            scenario = dict(cards[scenario_id])
            source_ids = set(scenario.get("source_chunk_ids") or [])
            related_chunks = [row for row in chunks if row.get("chunk_id") in source_ids]
            scenario.setdefault("scenario_name", scenario.get("title") or scenario_id)
            scenario.setdefault(
                "input_data", list((scenario.get("controllable_variables") or {}).keys())
            )
            scenario.setdefault(
                "environment", list((scenario.get("environment_variables") or {}).keys())
            )
            scenario.setdefault(
                "trigger_event", "；".join(scenario.get("trigger_events") or [])
            )
            result.append({
                "project_id": request.project_id, "requirement_id": "",
                "case_type": request.case_type,
                "project_profile": self.manager.get_profile(request.project_id) or {},
                "requirement": {
                    "requirement_id": "", "title": scenario["scenario_name"],
                    "description": scenario.get("scenario_goal", ""),
                },
                "related_chunks": related_chunks,
                "related_scenario_cards": [scenario],
                "matched_test_methods": [], "six_quality_attributes": [],
                "similar_library_cases": [], "visual_evidence": [],
                "missing_information": list(scenario.get("missing_information") or []),
                "generation_constraints": ["必须按已批准场景生成，不得重算装备数量"],
                "context_id": "",
            })
        return result

    def _preflight_fallback_reason(self, request: GenerationRequest) -> str:
        if request.requested_mode == "rule":
            return "requested_rule_mode"
        if not self.settings.enable_agent:
            return "agent_disabled"
        if not self.settings.enable_ollama:
            return "ollama_disabled"
        if not self.health_client.is_available():
            return "ollama_unavailable"
        if not self.health_client.model_exists(self.settings.ollama_model):
            return f"ollama_model_missing:{self.settings.ollama_model}"
        return ""

    @staticmethod
    def _generation_package_is_complete(packages: List[Dict[str, Any]]) -> bool:
        return all(
            wrapped.get("package", {}).get("requirement")
            and wrapped.get("package", {}).get("atomic_requirements")
            for wrapped in packages
        )

    @staticmethod
    def _generation_metadata(request: GenerationRequest) -> Dict[str, Any]:
        selected = request.selected_test_type or request.case_type
        recommended = request.recommended_test_type
        return {
            "recommended_test_type": recommended,
            "selected_test_type": selected,
            "test_type_overridden": bool(
                request.test_type_overridden
                or (recommended and selected and recommended != selected)
            ),
            "test_type_confidence": float(request.test_type_confidence or 0),
            "test_type_reasons": list(request.test_type_reasons or []),
            "case_count": int(request.case_count),
            "auto_case_count": bool(request.auto_case_count),
            "batch_id": request.batch_id,
        }

    @staticmethod
    def _emit(callback: Callable[[dict[str, str]], None] | None, kind: str, content: str) -> None:
        if callback is not None:
            try: callback({"kind": kind, "content": content})
            except Exception: logger.exception("task_state_persistence_error: progress callback failed kind=%s",kind)

    def _generate_with_direct_model(
        self,
        packages: List[Dict[str, Any]],
        request: GenerationRequest,
        agent_failure: str,
        progress_callback: Callable[[dict[str, str]], None] | None = None,
        run_log: GenerationRunLog | None = None,
        cancellation_callback: Callable[[], bool] | None = None,
    ) -> List[TestCase]:
        if self.direct_model_generator is not None:
            return self._prepare_model_direct_cases(
                self.direct_model_generator(packages, request, agent_failure),
                request,
            )
        payload = self._direct_model_payload(packages, request, agent_failure)
        if run_log:
            run_log.artifact("03-model-request.json", {"mode": "direct", "payload": payload, "fingerprints": [item["fingerprint"] for item in packages]})
        payload["stream"] = bool(progress_callback)
        response = requests.post(
            f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=self.settings.ollama_timeout,
            stream=bool(progress_callback),
        )
        if not response.ok:
            logger.error("Generation Ollama HTTP %s response=%s", response.status_code, response_error_text(response))
        raise_for_ollama_status(response)
        if progress_callback:
            data: Dict[str, Any] = {}
            content_parts: List[str] = []
            def events():
                for line in response.iter_lines(decode_unicode=True):
                    if line:
                        event=json.loads(line)
                        thinking=str(((event.get("message") or {}).get("thinking")) or "")
                        if thinking: self._emit(progress_callback,"reasoning",thinking)
                        yield event
            try:
                data, content = StreamGuard(self.settings, cancellation_callback or (lambda: False)).collect(
                    events(), lambda part: self._emit(progress_callback, "token", part))
            except GenerationTerminated as exc:
                response.close()
                if run_log:
                    run_log.artifact("04-partial-model-response.json", {"content": exc.partial_output})
                    run_log.event("需求生成", "流式生成已终止", termination_reason=exc.reason,
                                  received_chars=len(exc.partial_output))
                raise StructuredOutputError(f"模型输出已终止：termination_reason={exc.reason}") from exc
            data["message"] = {**(data.get("message") or {}), "content": content}
        else:
            data = response.json()
        if run_log:
            run_log.artifact("04-model-raw-response.json", data)
        content = str(((data.get("message") or {}).get("content")) or "").strip()
        metrics = {key:data.get(key) for key in ("done_reason", "eval_count", "prompt_eval_count")}
        if run_log:
            run_log.event("需求生成", "Direct响应统计", **metrics)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            truncated = data.get("done_reason") == "length" or (
                int(data.get("eval_count") or 0) >= self.settings.ollama_structured_num_predict
            )
            if truncated:
                if run_log:
                    run_log.event("需求生成", "Direct输出截断", classification="output_truncated", **metrics)
                raise StructuredOutputError(
                    "output_truncated：模型达到 num_predict 且JSON未闭合；相同参数不重试。"
                    f" done_reason={data.get('done_reason')} eval_count={data.get('eval_count')} "
                    f"prompt_eval_count={data.get('prompt_eval_count')} num_predict={self.settings.ollama_structured_num_predict}"
                ) from exc
            raise StructuredOutputError(f"invalid_complete_json：模型声明完成但JSON无效：{exc}") from exc
        try:
            bundle = GeneratedCaseBundle.model_validate(parsed)
            cases = bundle.cases
        except ValidationError as exc:
            raise StructuredOutputError(
                "Direct 模型未返回完整 GeneratedCaseBundle；已停止，禁止转换为粗糙模板用例"
            ) from exc
        return self._prepare_model_direct_cases(cases, request)

    def _prepare_model_direct_cases(
        self, cases: List[TestCase], request: GenerationRequest
    ) -> List[TestCase]:
        repaired_cases = [
            self._repair_step_result_alignment(case).model_copy(
                update={"generation_mode": "model_direct"}
            )
            for case in cases
        ]
        self._validate_agent_cases(repaired_cases, request)
        return repaired_cases

    @staticmethod
    def _validate_and_render_detailed_cases(
        cases: List[TestCase], packages: List[Dict[str, Any]], case_type: str = "功能测试"
    ) -> List[TestCase]:
        states = {str(wrapped.get("package", {}).get("html_evidence_state") or "no_html_evidence") for wrapped in packages}
        candidate_mode = "machine_unmatched_candidate_pool" in states
        valid_pages = {
            str(page.get("page_id") or "")
            for wrapped in packages
            for page in wrapped.get("package", {}).get("page_evidence", [])
            if page.get("binding_status") in {"confirmed", "page_confirmed_element_pending"} and page.get("page_id")
        }
        valid_elements = {
            str(element.get("element_id") or "")
            for wrapped in packages
            for page in wrapped.get("package", {}).get("page_evidence", [])
            if page.get("binding_status") in {"confirmed", "page_confirmed_element_pending"}
            for element in page.get("elements", [])
            if element.get("element_id") and element.get("binding_status") in {"confirmed", "selectable_on_generation"}
        }
        element_evidence: Dict[str, Dict[str, Any]] = {}
        candidate_pages: set[str] = set()
        candidate_elements: set[str] = set()
        observed_messages: set[str] = set()
        requirement_text = ""
        for wrapped in packages:
            requirement_text += json.dumps(
                wrapped.get("package", {}).get("requirement", {}), ensure_ascii=False
            )
            for page in wrapped.get("package", {}).get("page_evidence", []):
                for observation in page.get("playwright_observations", []):
                    observed_messages.update(
                        str(value)
                        for value in (observation.get("observed_result") or {}).values()
                        if isinstance(value, str) and value
                    )
                for element in page.get("elements", []):
                    element_id = str(element.get("element_id") or "")
                    if element_id and element.get("binding_status") in {"confirmed", "selectable_on_generation"}:
                        element_evidence[element_id] = {
                            "page_id": page.get("page_id") or "",
                            "page_name": page.get("title") or page.get("page_name") or "",
                            "region": element.get("region") or element.get("relative_position") or element.get("semantic_position") or "",
                            "element_name": element.get("label") or element.get("text") or element.get("name") or "",
                            "element_type": element.get("element_type") or element.get("tag") or "",
                            "binding_status": "confirmed" if element.get("binding_status") == "confirmed" else "model_selected_unconfirmed",
                        }
            for page in wrapped.get("package", {}).get("candidate_pages", []):
                page_id = str(page.get("page_id") or "")
                if page_id:
                    candidate_pages.add(page_id)
                for observation in page.get("playwright_observations", []):
                    observed_messages.update(str(value) for value in (observation.get("observed_result") or {}).values() if isinstance(value, str) and value)
                for element in page.get("business_elements", []):
                    element_id = str(element.get("element_id") or "")
                    if not element_id:
                        continue
                    candidate_elements.add(element_id)
                    element_evidence[element_id] = {
                        "page_id": page_id, "page_name": page.get("page_name") or "",
                        "region": element.get("region") or element.get("relative_position") or element.get("position_phrase") or "",
                        "element_name": element.get("label") or element.get("name") or "",
                        "element_type": element.get("control_type") or "",
                        "binding_status": "model_selected_unconfirmed",
                    }
        allowed_pages = valid_pages | candidate_pages
        allowed_elements = valid_elements | candidate_elements
        rendered: List[TestCase] = []
        for case in cases:
            if valid_pages and not case.structured_steps:
                raise StructuredOutputError(f"用例 {case.case_id} 缺少 structured_steps")
            if not case.structured_steps:
                rendered.append(case)
                continue
            instructions: List[str] = []
            expected: List[str] = []
            normalized_steps = []
            case_needs_confirmation = case.need_human_confirm
            for index, original_step in enumerate(case.structured_steps, 1):
                step = original_step
                if not step.element_id and step.action in {"click", "input", "select", "check"}:
                    matches = [
                        element_id
                        for element_id, item in element_evidence.items()
                        if item.get("element_name") and str(item["element_name"]) in step.instruction
                    ]
                    if len(matches) == 1:
                        step = step.model_copy(update={"element_id": matches[0]})
                evidence = element_evidence.get(step.element_id)
                if evidence:
                    element_name = step.element_name or str(evidence["element_name"])
                    if element_name and "〖" not in element_name:
                        element_name = f"〖{element_name}〗"
                    step = step.model_copy(update={
                        "page_id": step.page_id or evidence["page_id"],
                        "page_name": step.page_name or evidence["page_name"],
                        "region": step.region or evidence["region"],
                        "element_name": element_name,
                        "element_type": step.element_type or evidence["element_type"],
                        "binding_status": evidence["binding_status"],
                        "need_human_confirm": step.need_human_confirm or evidence["binding_status"] == "model_selected_unconfirmed",
                    })
                    if evidence["binding_status"] == "model_selected_unconfirmed":
                        if not step.selection_reason.strip():
                            step = step.model_copy(update={"selection_reason":"模型选择了真实候选元素但未返回关联理由；具体关联理由待人工确认"})
                        case_needs_confirmation = True
                if (not allowed_pages or (candidate_mode and not step.page_id and not step.element_id)) and step.action in {"click", "input", "select", "check"}:
                    step = step.model_copy(update={
                        "page_id":"", "page_name":"", "region":"", "element_id":"",
                        "element_name":"", "element_type":"", "need_human_confirm":True,
                    })
                    case_needs_confirmation = True
                if step.step_no != index:
                    raise StructuredOutputError(f"用例 {case.case_id} 步骤编号不连续")
                if step.page_id and step.page_id not in allowed_pages:
                    raise StructuredOutputError(f"用例 {case.case_id} 使用不存在的证据页面 {step.page_id}")
                if step.element_id and step.element_id not in allowed_elements:
                    raise StructuredOutputError(f"用例 {case.case_id} 使用不存在的证据元素 {step.element_id}")
                if evidence and step.page_id and str(evidence["page_id"]) != step.page_id:
                    raise StructuredOutputError(f"用例 {case.case_id} 的元素 {step.element_id} 不属于页面 {step.page_id}")
                if step.action in {"click", "input", "select", "check"}:
                    if valid_pages or (candidate_mode and (step.page_id or step.element_id)):
                        if not step.page_id or not step.page_name or not step.region:
                            raise StructuredOutputError(f"用例 {case.case_id} 第{index}步缺少已确认页面或真实区域")
                        if not step.element_id or not evidence:
                            raise StructuredOutputError(f"用例 {case.case_id} 第{index}步缺少真实目录元素")
                        if not evidence.get("region"):
                            raise StructuredOutputError(f"用例 {case.case_id} 第{index}步真实元素缺少区域证据")
                        if not step.element_name or "〖" not in step.element_name or "〗" not in step.element_name:
                            raise StructuredOutputError(f"用例 {case.case_id} 第{index}步控件名称未使用〖〗")
                if step.action in {"input", "select"} and not step.input_value:
                    raise StructuredOutputError(f"用例 {case.case_id} 第{index}步缺少具体输入值")
                result = step.expected_result
                if (
                    result.visible_message
                    and result.visible_message not in observed_messages
                    and result.visible_message not in requirement_text
                ):
                    result = result.model_copy(update={
                        "visible_message": "",
                        "online_confirmation": "页面实际提示内容缺少需求或观测证据，待联机确认",
                    })
                    step = step.model_copy(update={
                        "expected_result": result,
                        "need_human_confirm": True,
                    })
                    case_needs_confirmation = True
                result_parts = [result.page_change, result.element_change, result.visible_message, result.data_change, result.online_confirmation]
                observable = "；".join(item for item in result_parts if item)
                if not observable:
                    raise StructuredOutputError(f"用例 {case.case_id} 第{index}步缺少可观察预期")
                if any(word in observable for word in ("正常显示", "正确处理")):
                    raise StructuredOutputError(f"用例 {case.case_id} 第{index}步包含主观预期")
                if step.action in {"click", "input", "select", "check"}:
                    if step.page_name and step.element_name:
                        location = f"在〖{step.page_name}〗页面"
                        if step.region:
                            location += f"的{step.region}"
                        action_text = {
                            "click": f"点击{step.element_name}",
                            "input": f"在{step.element_name}中输入“{step.input_value}”",
                            "select": f"从{step.element_name}选择“{step.input_value}”",
                            "check": f"勾选{step.element_name}",
                        }[step.action]
                        instruction = f"{location}，{action_text}。"
                    else:
                        instruction = step.instruction
                else:
                    instruction = step.instruction
                instructions.append(instruction)
                expected.append(observable)
                normalized_steps.append(step)
            missing = list(case.missing_information)
            selected_page_ids = [step.page_id for step in normalized_steps if step.page_id]
            selected_element_ids = [step.element_id for step in normalized_steps if step.element_id]
            if candidate_mode and not selected_element_ids:
                case_needs_confirmation = True
                missing.append("具体页面元素待确认；生成模型未从当前项目未确认候选证据池中选择元素")
            elif case_needs_confirmation and not allowed_pages:
                missing.append("当前需求没有已确认HTML页面证据；当前项目缺少HTML候选，已按纯需求模式生成且未编造页面或元素")
            if case_needs_confirmation and "缺少可支持部分页面提示的需求或观测证据" not in missing:
                missing.append("缺少可支持部分页面提示的需求或观测证据")
            rendered.append(case.model_copy(update={"structured_steps": normalized_steps, "test_steps": instructions, "expected_results": expected, "need_human_confirm": case_needs_confirmation, "missing_information": missing,
                "page_ids":list(dict.fromkeys([*case.page_ids,*selected_page_ids])), "html_element_ids":list(dict.fromkeys([*case.html_element_ids,*selected_element_ids]))}))
        return rendered

    @staticmethod
    def _repair_step_result_alignment(case: TestCase) -> TestCase:
        if len(case.test_steps) == len(case.expected_results):
            return case
        pair_count = min(len(case.test_steps), len(case.expected_results))
        if pair_count <= 0:
            return case
        missing = list(case.missing_information)
        if len(case.test_steps) > pair_count:
            missing.append(
                "大模型输出存在未配对的测试步骤，已保留可配对部分并需人工确认"
            )
        if len(case.expected_results) > pair_count:
            missing.append(
                "大模型输出存在未配对的预期结果，已保留可配对部分并需人工确认"
            )
        return case.model_copy(
            update={
                "test_steps": case.test_steps[:pair_count],
                "expected_results": case.expected_results[:pair_count],
                "need_human_confirm": True,
                "missing_information": list(dict.fromkeys(missing)),
            }
        )

    def _coerce_direct_model_json_to_cases(
        self,
        parsed: Any,
        contexts: List[Dict[str, Any]],
        request: GenerationRequest,
    ) -> List[TestCase]:
        if not isinstance(parsed, dict):
            raise StructuredOutputError("Direct model returned non-object JSON")
        context = contexts[0] if contexts else {}
        requirement = dict(context.get("requirement") or {})
        requirement_id = (
            request.requirement_ids[0]
            if request.requirement_ids
            else str(context.get("requirement_id") or "")
        )
        title = (
            str(requirement.get("title") or requirement.get("name") or "").strip()
            or str(parsed.get("title") or parsed.get("测试用例标题") or "").strip()
            or f"{request.case_type} for {requirement_id}"
        )
        description = str(
            requirement.get("description")
            or requirement.get("functional_description")
            or requirement.get("requirement_text")
            or requirement.get("text")
            or ""
        ).strip()
        flat_notes = self._flatten_model_json(parsed)
        test_data = self._strings_from_model_json(
            parsed,
            ("场景输入数据", "输入数据", "test_data", "input_data", "inputs"),
        )
        thresholds = self._strings_from_model_json(
            parsed,
            ("性能判定阈值", "验收标准", "acceptance_criteria", "thresholds"),
        )
        source_chunk_ids = [
            str(row.get("chunk_id") or "")
            for row in context.get("related_chunks") or context.get("source_excerpt") or []
            if row.get("chunk_id")
        ]
        source_documents = list(
            dict.fromkeys(
                str(row.get("filename") or row.get("source_document") or "")
                for row in context.get("related_chunks") or context.get("source_excerpt") or []
                if row.get("filename") or row.get("source_document")
            )
        )
        step = (
            f"Design and execute a {request.case_type} case for {requirement_id}: "
            f"{description or title}"
        )
        expected = (
            "Observed result satisfies the requirement"
            + (f" and thresholds: {'; '.join(thresholds[:5])}" if thresholds else ".")
        )
        return [
            TestCase(
                case_id="TC-MODEL-001",
                title=title,
                objective=description or title,
                preconditions=self._strings_from_model_json(
                    parsed, ("前置条件", "preconditions", "conditions")
                ),
                test_steps=[step],
                expected_results=[expected],
                evaluation_criteria=(
                    "; ".join(thresholds[:8])
                    or "The observable result matches the selected requirement."
                ),
                test_data=test_data,
                environment=self._strings_from_model_json(
                    parsed, ("测试环境", "environment", "明确测试对象")
                ),
                requirement_ids=[requirement_id] if requirement_id else [],
                scenario_ids=list(request.scenario_ids),
                source_chunk_ids=list(dict.fromkeys(source_chunk_ids)),
                source_documents=source_documents,
                quality_category=[request.case_type],
                test_method=request.case_type,
                need_human_confirm=True,
                missing_information=[
                    "Direct model returned analysis JSON instead of GeneratedCaseBundle; converted to a reviewable test case.",
                    *flat_notes[:8],
                ],
                generation_mode="model_direct",
            )
        ]

    @classmethod
    def _strings_from_model_json(
        cls, parsed: Dict[str, Any], keys: tuple[str, ...]
    ) -> List[str]:
        values: List[str] = []
        for key in keys:
            if key in parsed:
                values.extend(cls._flatten_value(parsed[key]))
        return list(dict.fromkeys(item for item in values if item))

    @classmethod
    def _flatten_model_json(cls, parsed: Dict[str, Any]) -> List[str]:
        values: List[str] = []
        for key, value in parsed.items():
            if key == "cases":
                continue
            for item in cls._flatten_value(value):
                values.append(f"{key}: {item}")
        return list(dict.fromkeys(values))

    @classmethod
    def _flatten_value(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, (int, float, bool)):
            return [str(value)]
        if isinstance(value, list):
            result: List[str] = []
            for item in value:
                result.extend(cls._flatten_value(item))
            return result
        if isinstance(value, dict):
            result = []
            for key, item in value.items():
                flattened = cls._flatten_value(item)
                if flattened:
                    result.extend(f"{key}: {part}" for part in flattened)
            return result
        text = str(value).strip()
        return [text] if text else []

    def _direct_model_payload(
        self,
        packages: List[Dict[str, Any]],
        request: GenerationRequest,
        agent_failure: str,
    ) -> Dict[str, Any]:
        canonical_packages = [item["package"] for item in packages]
        schema_hint = {
            "cases": [
                {
                    "case_id": "TC-MODEL-001",
                    "title": "string",
                    "objective": "string",
                    "preconditions": ["string"],
                    "test_steps": ["string"],
                    "expected_results": ["string"],
                    "evaluation_criteria": "string",
                    "test_data": ["string"],
                    "environment": ["string"],
                    "requirement_ids": request.requirement_ids,
                    "scenario_ids": request.scenario_ids,
                    "source_chunk_ids": ["only ids from related_chunks"],
                    "source_documents": ["string"],
                    "quality_category": [request.case_type],
                    "test_method": request.case_type,
                    "need_human_confirm": False,
                    "missing_information": [],
                    "generation_mode": "model_direct",
                    "structured_steps": [{
                        "step_no": 1, "page_id": "from GenerationPackage or empty",
                        "page_name": "string", "region": "string",
                        "element_id": "from GenerationPackage or empty",
                        "element_name": "〖控件名称〗", "element_type": "string",
                        "action": "click", "input_value": "",
                        "instruction": "在页面和区域中执行一个动作",
                        "expected_result": {"page_change": "", "element_change": "可观察变化", "visible_message": "", "data_change": "", "online_confirmation": ""},
                        "evidence_source": "requirement/html/playwright", "need_human_confirm": False,
                        "binding_status": "confirmed/model_selected_unconfirmed/empty", "selection_reason": "string",
                    }],
                }
            ],
            "coverage_plan": [{"indicator_id": "string", "case_ids": ["TC-MODEL-001"], "scenario_types": ["normal"]}],
            "coverage_result": {"covered_indicator_ids": [], "uncovered_indicator_ids": []},
            "overall_missing_information": [],
            "used_tool_names": [],
            "retrieved_source_chunk_ids": [],
            "knowledge_unit_ids": [],
            "equipment_ids": [],
            "configuration_rule_ids": [],
            "scenario_validation_run_id": "",
            "warnings": [],
        }
        quantity_rule = (
            "- Decide the useful number of cases yourself and cover both positive and negative paths without duplicates.\n"
            if request.auto_case_count
            else "- Generate the requested number of cases.\n"
        )
        prompt = (
            "你是当前项目的测试用例生成模型。LangChain Agent 工具链失败，但你仍必须只使用"
            "下面提供的项目上下文生成结构化 JSON。\n"
            f"Agent failure: {agent_failure}\n"
            f"目标需求: {request.requirement_ids}\n"
            f"目标场景: {request.scenario_ids}\n"
            f"测试类型: {request.case_type}\n"
            f"用例数量: {request.case_count}\n"
            "要求：test_steps 与 expected_results 数量必须相等且一一对应；"
            "requirement_ids 只能使用目标需求；source_chunk_ids 只能使用上下文中的 chunk_id；"
            "信息不足必须写入 missing_information。\n"
            "只输出 JSON，不要 Markdown，不要解释。\n"
            "JSON 结构示例：\n"
            + json.dumps(schema_hint, ensure_ascii=False)
            + "\n项目上下文：\n"
            + json.dumps(canonical_packages, ensure_ascii=False, default=str)
        )
        prompt = (
            "You are a test-case generation model. The LangChain Agent failed, "
            "but you must still generate test cases only from the supplied project context.\n"
            f"Agent failure: {agent_failure}\n"
            f"Target requirement_ids: {request.requirement_ids}\n"
            f"Target scenario_ids: {request.scenario_ids}\n"
            f"Selected test type: {request.case_type}\n"
            f"Case count limit: {request.case_count}\n"
            f"Automatic count: {request.auto_case_count}\n\n"
            "Return exactly one JSON object matching this schema. The top-level object "
            "must contain the key 'cases'. Do not output analysis keys such as user_profile, "
            "scenario_cards, test_objects, input_data_summary, thresholds, or any Chinese "
            "analysis headings at the top level.\n"
            "Rules:\n"
            "- cases length must be no more than Case count.\n"
            + quantity_rule
            +
            "- test_steps and expected_results must have the same length and align by index.\n"
            "- Generate all cases for this one requirement in this single response; include coverage_plan and coverage_result.\n"
            "- Every case must include structured_steps. One step has exactly one action. UI steps must name page/region and wrap controls in 〖〗.\n"
            "- page_id and element_id must exist in GenerationPackage; without confirmed evidence do not invent UI details.\n"
            "- If html_evidence_state is machine_unmatched_candidate_pool, reassess candidate_pages using the full requirement, inputs, processing, outputs, and atomic requirements. machine_unmatched is a prior model opinion, not an exclusion.\n"
            "- Choose only candidate page_id values and only business_elements belonging to that page. Never invent a page, region, or control. Copy region/position evidence; set binding_status=model_selected_unconfirmed, need_human_confirm=true, and explain selection_reason.\n"
            "- If no candidate is defensible, generate requirement-grounded non-UI steps, leave page_id/element_id empty, and say that the concrete page or element needs human confirmation.\n"
            "- requirement_ids may only use Target requirement_ids.\n"
            "- source_chunk_ids may only use chunk_id values present in project_context.\n"
            "- Generate cases for the selected test type, not the recommended type if different.\n"
            "- Output JSON only. No markdown. No explanation.\n\n"
            "Required JSON schema example:\n"
            + json.dumps(schema_hint, ensure_ascii=False)
            + "\n\nproject_context:\n"
            + json.dumps(canonical_packages, ensure_ascii=False, default=str)
        )
        return {
            "model": self.settings.ollama_model,
            "messages": [
                {"role": "system", "content": "只输出满足用户给定结构的 JSON。"},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": GeneratedCaseBundle.model_json_schema(),
            "options": {
                "temperature": self.settings.ollama_temperature,
                "num_ctx": self.settings.ollama_num_ctx,
                "num_predict": self.settings.ollama_structured_num_predict,
            },
        }

    def _record_test_type_feedback(self, request: GenerationRequest) -> None:
        metadata = self._generation_metadata(request)
        if not metadata["test_type_overridden"]:
            return
        requirement_id = request.requirement_ids[0] if request.requirement_ids else ""
        requirement = (
            self.manager.get_requirement(request.project_id, requirement_id)
            if requirement_id
            else {}
        ) or {}
        payload = {
            "requirement_id": requirement_id,
            "requirement_summary": str(
                requirement.get("description") or requirement.get("title") or ""
            )[:500],
            "recommended_test_type": metadata["recommended_test_type"],
            "selected_test_type": metadata["selected_test_type"],
            "test_type_confidence": metadata["test_type_confidence"],
            "test_type_reasons": metadata["test_type_reasons"],
            "project_id": request.project_id,
            "source_documents": requirement.get("source_documents")
            or [requirement.get("source_document", "")],
            "created_at": now_iso(),
        }
        try:
            with self.manager.connections.transaction() as conn:
                conn.execute(
                    """INSERT INTO feedback_candidates(
                    candidate_id,project_id,candidate_type,target_artifact_type,
                    target_artifact_id,feedback_json,status,document_id,chunk_id,
                    page_no,jsonl_record_no,created_at,updated_at,candidate_key,occurrence_count
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        new_id("FDB"),
                        request.project_id,
                        "test_type_override",
                        "requirement",
                        requirement_id,
                        dumps_json(payload),
                        "pending",
                        "",
                        "",
                        None,
                        None,
                        payload["created_at"],
                        payload["created_at"],
                        f"test_type_override:{request.project_id}:{requirement_id}:{metadata['recommended_test_type']}:{metadata['selected_test_type']}",
                        1,
                    ),
                )
        except Exception:
            # Feedback capture is useful for learning but must never block generation.
            return

    @staticmethod
    def _validate_agent_cases(
        cases: List[TestCase], request: GenerationRequest
    ) -> None:
        if not cases or len(cases) > request.case_count:
            raise StructuredOutputError("Agent returned an invalid number of cases")
        ids = [case.case_id for case in cases]
        if len(ids) != len(set(ids)):
            raise StructuredOutputError("Agent returned duplicate case_id values")
        allowed_requirements = set(request.requirement_ids)
        for case in cases:
            if not set(case.requirement_ids).issubset(allowed_requirements):
                raise StructuredOutputError(
                    f"Case {case.case_id} references requirements outside the request"
                )
            if len(case.test_steps) != len(case.expected_results):
                raise StructuredOutputError(
                    f"Case {case.case_id} steps and expected results do not align"
                )

    def _score_and_persist(
        self,
        request: GenerationRequest,
        contexts: List[Dict[str, Any]],
        cases: List[TestCase],
        run_id: str,
        mode: GenerationMode,
        fallback_reason: str,
        generation_metadata: Dict[str, Any],
    ) -> List[GeneratedCaseRecord]:
        context_by_requirement = {
            str(context.get("requirement_id") or ""): context for context in contexts
        }
        self._complete_traceability(cases, context_by_requirement, contexts, request)
        records: List[GeneratedCaseRecord] = []
        for case in cases:
            requirement_id = (
                case.requirement_ids[0]
                if case.requirement_ids
                else (request.requirement_ids[0] if request.requirement_ids else "")
            )
            context = context_by_requirement.get(requirement_id) or contexts[0]
            persistence_data = self._to_persistence_data(
                case,
                context,
                request.project_id,
                requirement_id,
                request.case_type,
                run_id,
                mode,
                fallback_reason,
                generation_metadata,
            )
            quality = evaluate_case_quality(persistence_data, context)
            persistence_data["quality_score"] = quality["score"]
            persistence_data["quality_issues"] = quality["issues"]
            valid_chunks = {
                str(row.get("chunk_id") or ""): row
                for row in context.get("related_chunks") or []
            }
            source_rows = [
                valid_chunks[chunk_id]
                for chunk_id in case.source_chunk_ids
                if chunk_id in valid_chunks
            ]
            try:
                self.manager.save_generated_case(
                    request.project_id, persistence_data, run_id, source_rows
                )
                self.manager.save_quality_score(
                    request.project_id,
                    case.case_id,
                    str(context.get("context_id") or ""),
                    quality,
                )
                self.manager.save_review_result(
                    request.project_id,
                    case.case_id,
                    "统一生成规则质量评价",
                    "需修改" if quality["issues"] else "通过",
                    quality["issues"],
                )
            except (ValueError, TypeError, OSError) as exc:
                raise PersistenceError(
                    f"Failed to persist generated case {case.case_id}: {exc!r}"
                ) from exc
            records.append(
                GeneratedCaseRecord(
                    case=case,
                    persistence_data=persistence_data,
                    quality=quality,
                    context_id=str(context.get("context_id") or ""),
                )
            )
        return records

    @staticmethod
    def _complete_traceability(
        cases: List[TestCase],
        context_by_requirement: Dict[str, Dict[str, Any]],
        contexts: List[Dict[str, Any]],
        request: GenerationRequest,
    ) -> None:
        """Attach reviewed evidence at the canonical persistence boundary.

        Model and deterministic-fallback generators are both allowed to omit
        traceability fields.  Persisting those omissions silently produces a
        false zero-coverage report, so fill only project-scoped facts already
        present in the generation context and distribute every atom to at least
        one case for its function.
        """
        grouped: Dict[str, List[TestCase]] = {}
        for case in cases:
            requirement_id = (
                case.requirement_ids[0]
                if case.requirement_ids
                else (request.requirement_ids[0] if request.requirement_ids else "")
            )
            grouped.setdefault(requirement_id, []).append(case)

        for requirement_id, requirement_cases in grouped.items():
            context = context_by_requirement.get(requirement_id) or contexts[0]
            trace = context.get("traceability_context") or {}
            atom_ids = [
                str(row.get("indicator_id") or "")
                for row in trace.get("atomic_requirements") or []
                if row.get("indicator_id")
            ]
            valid_atoms = set(atom_ids)
            covered: set[str] = set()
            for case in requirement_cases:
                case.indicator_ids = [
                    item for item in dict.fromkeys(case.indicator_ids) if item in valid_atoms
                ]
                covered.update(case.indicator_ids)
            for index, atom_id in enumerate(item for item in atom_ids if item not in covered):
                target = requirement_cases[index % len(requirement_cases)]
                target.indicator_ids = list(dict.fromkeys([*target.indicator_ids, atom_id]))

            page_ids = [
                str(row.get("page_id") or "")
                for row in trace.get("requirement_page_links") or []
                if row.get("page_id") and str(row.get("status") or "") in {"confirmed", "page_confirmed_element_pending"}
            ]
            element_ids = [
                str(row.get("confirmed_element_id") or "")
                for row in trace.get("requirement_element_links") or []
                if row.get("confirmed_element_id")
                and str(row.get("status") or "") in {"confirmed", "page_confirmed_element_pending"}
            ]
            for case in requirement_cases:
                case.function_id = case.function_id or requirement_id
                case.page_ids = list(dict.fromkeys([*case.page_ids, *page_ids]))
                case.html_element_ids = list(
                    dict.fromkeys([*case.html_element_ids, *element_ids])
                )
                selected_pages = set(case.page_ids)
                observation_ids = [
                    str(row.get("observation_id") or "")
                    for row in trace.get("playwright_observations") or []
                    if row.get("observation_id")
                    and (not selected_pages or str(row.get("page_id") or "") in selected_pages)
                ]
                case.playwright_observation_ids = list(
                    dict.fromkeys([*case.playwright_observation_ids, *observation_ids])
                )

    @staticmethod
    def _to_persistence_data(
        case: TestCase,
        context: Dict[str, Any],
        project_id: str,
        requirement_id: str,
        case_type: str,
        run_id: str,
        mode: GenerationMode,
        fallback_reason: str,
        generation_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Explicit database/export adapter; domain serialization remains canonical."""
        data = case.to_persistence_dict()
        scenarios = context.get("related_scenario_cards") or []
        related_scenario = (
            str(scenarios[0].get("scenario_name") or "") if scenarios else ""
        )
        data.update(
            {
                "project_id": project_id,
                "requirement_id": requirement_id,
                "scenario_id": case.scenario_ids[0] if case.scenario_ids else "",
                "related_scenario": related_scenario,
                "case_type": case_type,
                "case_name": case.title,
                "test_purpose": case.objective,
                "prerequisites": case.preconditions,
                "input_data": case.test_data,
                "test_environment": case.environment,
                "expected_result": case.expected_results,
                "pass_criteria": case.evaluation_criteria,
                "generation_mode": mode,
                "fallback_reason": fallback_reason,
                "generation_run_id": run_id,
                "provenance": dict(generation_metadata),
            }
        )
        return data


def generate_test_cases(
    request: GenerationRequest,
    manager: Any,
    case_library: Optional[Any] = None,
) -> GenerationResult:
    """Functional convenience entry point for callers that do not retain a service."""
    return GenerationService(manager, case_library).generate_test_cases(request)
