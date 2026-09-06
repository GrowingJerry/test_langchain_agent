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
from application.services.coverage_generation import (
    CaseDeduplicator, CoveragePlanner, CoverageReconciler, GenerationBatch, GenerationBatchPlanner,
)
from domain.schemas.coverage import TestPoint


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
    generation_strategy: Literal["legacy", "atomic_complete", "manual_total_limit"] = "atomic_complete"
    max_cases_per_atom: int = Field(default=10, ge=1, le=20)
    manual_total_case_limit: int | None = Field(default=None, ge=1, le=500)
    coverage_types: List[str] = Field(default_factory=lambda: ["normal", "abnormal", "boundary", "state", "constraint", "recovery"])
    atomic_requirement_id: str = ""
    target_test_points: List[Dict[str, Any]] = Field(default_factory=list)

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
    review_status: str = "ready"


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
    valid_case_count: int = 0
    review_case_count: int = 0
    rejected_case_count: int = 0
    rejected_cases: List[Dict[str, Any]] = Field(default_factory=list)
    coverage_status: str = ""
    planned_test_point_ids: List[str] = Field(default_factory=list)
    missing_test_point_ids: List[str] = Field(default_factory=list)


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

    def generate_atomic_coverage(
        self, request: GenerationRequest,
        progress_callback: Callable[[dict[str, str]], None] | None = None,
        cancellation_callback: Callable[[], bool] | None = None,
    ) -> GenerationResult:
        """Plan per atom, generate bounded batches, repair omissions, and checkpoint."""
        if len(request.requirement_ids) != 1:
            raise ValueError("atomic coverage generation requires exactly one requirement")
        requirement_id = request.requirement_ids[0]
        context = self._build_contexts(request)[0]
        trace = context.get("traceability_context") or {}
        atoms = list(trace.get("atomic_requirements") or [])
        if not atoms:
            # Old projects remain usable without TestPoint data.
            return self.generate_test_cases(request.model_copy(update={"generation_strategy": "legacy"}), progress_callback, cancellation_callback)
        planner = CoveragePlanner(self.settings)
        max_per_atom = min(request.max_cases_per_atom, self.settings.generation_absolute_max_cases_per_atom)
        all_points: list[TestPoint] = []
        fingerprint = request.batch_id or new_id("COV")
        with self.manager.connections.connection() as conn:
            existing = [dict(x) for x in conn.execute(
                "SELECT * FROM coverage_test_points WHERE project_id=? AND requirement_id=? ORDER BY atomic_requirement_id,test_point_id",
                (request.project_id, requirement_id)).fetchall()]
        for row in existing:
            metadata = json.loads(row.get("metadata_json") or "{}")
            all_points.append(TestPoint(test_point_id=row["test_point_id"], atomic_requirement_id=row["atomic_requirement_id"],
                requirement_id=row["requirement_id"], title=row["title"], scenario_type=row["scenario_type"],
                description=row["description"], priority=row["priority"], evidence_requirement=row["evidence_requirement"], metadata=metadata))
        planned_atoms = {point.atomic_requirement_id for point in all_points}
        for index, atom in enumerate(atoms, 1):
            if cancellation_callback and cancellation_callback():
                raise GenerationTerminated("client_cancelled", "")
            atom_id = str(atom.get("indicator_id") or "")
            if atom_id in planned_atoms:
                self._emit(progress_callback, "progress", f"{atom_id} planning checkpoint reused ({index}/{len(atoms)})")
                continue
            self._emit(progress_callback, "progress", f"{atom_id} planning ({index}/{len(atoms)})")
            evidence = {
                "requirement_page_links": [x for x in trace.get("requirement_page_links") or [] if str(x.get("function_id") or "") in {"", str(atom.get("function_id") or "")}],
                "requirement_element_links": [x for x in trace.get("requirement_element_links") or [] if str(x.get("indicator_id") or "") == atom_id],
                "candidate_pages": context.get("candidate_pages") or [],
                "playwright_observations": trace.get("playwright_observations") or [],
            }
            points = planner.plan_atom(requirement_id=requirement_id, atom=atom, evidence=evidence,
                coverage_types=request.coverage_types, max_cases=max_per_atom)
            all_points.extend(points)
            with self.manager.connections.transaction() as conn:
                for point in points:
                    conn.execute("""INSERT OR REPLACE INTO coverage_test_points(
                        project_id,requirement_id,atomic_requirement_id,test_point_id,title,scenario_type,
                        description,priority,evidence_requirement,status,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,'pending',?)""",
                        (request.project_id, requirement_id, atom_id, point.test_point_id, point.title,
                         point.scenario_type, point.description, point.priority, point.evidence_requirement,
                         dumps_json(point.metadata)))
        if not all_points:
            raise StructuredOutputError("Coverage Planner 未返回具有独立测试价值的测试点")
        if len(all_points) > self.settings.generation_hard_max_cases_per_requirement:
            raise StructuredOutputError("coverage plan exceeded GENERATION_HARD_MAX_CASES_PER_REQUIREMENT fuse")
        generation_points = (all_points[:request.manual_total_case_limit]
                             if request.manual_total_case_limit else all_points)
        records: list[GeneratedCaseRecord] = []
        existing_cases: list[TestCase] = []
        for row in self.manager.list_generated_cases(request.project_id):
            raw = row.get("case_json") or {}
            if row.get("requirement_id") == requirement_id and raw.get("test_point_id"):
                try:
                    existing_cases.append(TestCase.model_validate(raw))
                except ValidationError:
                    continue
        modes: list[str] = []
        repair_round = 0
        terminal_review: set[str] = set()
        already_generated = {case.test_point_id for case in existing_cases}
        pending = [point for point in generation_points if point.test_point_id not in already_generated]
        audit = CoverageReconciler.audit(all_points, existing_cases)
        while pending:
            pending_atoms = {point.atomic_requirement_id for point in pending}
            preview_capacities = []
            for atom_id in pending_atoms:
                scoped = self._scope_atomic_context(context, atom_id)
                preview = build_generation_package(self.manager, scoped,
                    num_ctx=self.settings.ollama_num_ctx,
                    num_predict=self.settings.ollama_structured_num_predict,
                    case_count=1, case_type=request.case_type, settings=self.settings)
                preview_capacities.append(preview["available_output_tokens"])
            safe_available = min(preview_capacities or [self.settings.ollama_structured_num_predict])
            effective_capacity = max(1, min(self.settings.generation_max_cases_per_model_call,
                max(0, safe_available - self.settings.generation_expected_output_base_tokens)
                // self.settings.generation_expected_tokens_per_case))
            round_batches = GenerationBatchPlanner(self.settings).plan(requirement_id, pending,
                available_output_tokens=safe_available)
            batch_queue = list(round_batches)
            batch_index = 0
            while batch_queue:
                batch = batch_queue.pop(0)
                batch_index += 1
                if cancellation_callback and cancellation_callback():
                    audit = CoverageReconciler.audit(all_points, [*existing_cases, *[x.case for x in records]])
                    return GenerationResult(project_id=request.project_id, generation_run_id=fingerprint,
                        generation_mode="model_direct", cases=records, coverage_status="cancelled",
                        planned_test_point_ids=audit.planned_test_point_ids, missing_test_point_ids=audit.missing_test_point_ids)
                estimated_output = (self.settings.generation_expected_output_base_tokens
                    + len(batch.test_points) * self.settings.generation_expected_tokens_per_case)
                self._emit(progress_callback, "progress",
                    f"atomic={batch.atomic_requirement_id}; batch={batch.batch_id}; "
                    f"test_points={len(batch.test_points)}; estimated_output_tokens={estimated_output}; "
                    f"num_predict={self.settings.ollama_structured_num_predict}; safe_capacity={effective_capacity}; "
                    f"accepted ({batch_index}/{batch_index + len(batch_queue)})")
                target = [x.model_dump(mode="json") for x in batch.test_points]
                child = request.model_copy(update={"generation_strategy": "legacy", "case_count": len(batch.test_points),
                    "atomic_requirement_id": batch.atomic_requirement_id, "target_test_points": target,
                    "batch_id": batch.batch_id,
                    "additional_instructions": request.additional_instructions + "\n仅生成 target_test_points 中的测试点；每条用例必须原样返回 test_point_id 和 atomic_requirement_id。"})
                try:
                    result = self.generate_test_cases(child, progress_callback, cancellation_callback)
                except StructuredOutputError as exc:
                    if "output_truncated" in str(exc) and len(batch.test_points) > 1:
                        midpoint = len(batch.test_points) // 2
                        left, right = batch.test_points[:midpoint], batch.test_points[midpoint:]
                        split = []
                        for suffix, points in (("L", left), ("R", right)):
                            split.append(GenerationBatch(f"{batch.batch_id}-{suffix}", batch.atomic_requirement_id, points))
                        batch_queue[0:0] = split
                        self._emit(progress_callback, "progress",
                            f"batch={batch.batch_id} output_truncated; split {len(batch.test_points)} -> {len(left)}+{len(right)}")
                        continue
                    if "output_truncated" in str(exc) and len(batch.test_points) == 1:
                        point = batch.test_points[0]
                        with self.manager.connections.transaction() as conn:
                            conn.execute("UPDATE coverage_test_points SET status='needs_review',generation_batch_id=?,updated_at=? WHERE project_id=? AND test_point_id=?",
                                (batch.batch_id, now_iso(), request.project_id, point.test_point_id))
                            conn.execute("""INSERT OR REPLACE INTO atomic_generation_checkpoints(
                                project_id,requirement_id,atomic_requirement_id,request_fingerprint,status,planned_count,
                                generated_count,missing_test_point_ids_json,repair_round,last_error,updated_at
                                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                (request.project_id, requirement_id, batch.atomic_requirement_id, fingerprint,
                                 "needs_review", 1, 0, dumps_json([point.test_point_id]), repair_round,
                                 str(exc), now_iso()))
                        self._emit(progress_callback, "error",
                            f"test_point={point.test_point_id} 单点仍截断，已标记 needs_review；继续后续批次")
                        terminal_review.add(point.test_point_id)
                        continue
                    raise
                records.extend(result.cases); modes.append(result.generation_mode)
                if len(existing_cases) + len(records) > self.settings.generation_hard_max_cases_per_requirement:
                    raise StructuredOutputError("generated cases exceeded GENERATION_HARD_MAX_CASES_PER_REQUIREMENT fuse")
                with self.manager.connections.transaction() as conn:
                    generated_ids = {x.case.test_point_id for x in result.cases}
                    for point in batch.test_points:
                        conn.execute("UPDATE coverage_test_points SET status=?,generation_batch_id=?,updated_at=? WHERE project_id=? AND test_point_id=?",
                            ("generated" if point.test_point_id in generated_ids else "missing", batch.batch_id, now_iso(), request.project_id, point.test_point_id))
                    conn.execute("""INSERT OR REPLACE INTO atomic_generation_checkpoints(
                        project_id,requirement_id,atomic_requirement_id,request_fingerprint,status,planned_count,
                        generated_count,missing_test_point_ids_json,repair_round,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (request.project_id,requirement_id,batch.atomic_requirement_id,fingerprint,"completed",
                         len(batch.test_points),len(generated_ids),dumps_json([x.test_point_id for x in batch.test_points if x.test_point_id not in generated_ids]),repair_round,now_iso()))
            audit = CoverageReconciler.audit(all_points, [*existing_cases, *[x.case for x in records]])
            if not audit.missing_test_point_ids:
                break
            if (request.manual_total_case_limit
                    or not self.settings.generation_coverage_repair_enabled
                    or repair_round >= self.settings.generation_coverage_repair_max_rounds):
                audit = CoverageReconciler.audit(all_points, [*existing_cases, *[x.case for x in records]], repair_rounds_exhausted=True)
                break
            repair_round += 1
            self._emit(progress_callback, "progress", f"repairing {len(audit.missing_test_point_ids)} missing test points, round {repair_round}")
            missing = set(audit.missing_test_point_ids)
            pending = [x for x in all_points if x.test_point_id in missing and x.test_point_id not in terminal_review]
            if not pending:
                audit = CoverageReconciler.audit(all_points, [*existing_cases, *[x.case for x in records]], repair_rounds_exhausted=True)
                break
        cases = CaseDeduplicator.deduplicate(x.case for x in records)
        audit = CoverageReconciler.audit(all_points, [*existing_cases, *cases], bool(audit.missing_test_point_ids))
        kept = {x.case.case_id for x in records if x.case in cases}
        records = [x for x in records if x.case.case_id in kept]
        return GenerationResult(project_id=request.project_id, generation_run_id=fingerprint,
            generation_mode=(modes[-1] if modes else "model_direct"), cases=records,
            valid_case_count=sum(x.review_status == "ready" for x in records),
            review_case_count=sum(x.review_status != "ready" for x in records),
            coverage_status=audit.coverage_status, planned_test_point_ids=audit.planned_test_point_ids,
            missing_test_point_ids=audit.missing_test_point_ids)

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
        run_log.event("CONFIG_SNAPSHOT", "最终生效配置", batch_id=request.batch_id,
            requirement_id=request.requirement_ids[0] if request.requirement_ids else "",
            atomic_requirement_id=request.atomic_requirement_id, model_call_id=run_log.run_id,
            model=self.settings.test_case_model, generation_mode=request.requested_mode,
            generation_strategy=request.generation_strategy,
            **{name:getattr(self.settings,name) for name in (
                "ollama_timeout","ollama_num_ctx","ollama_structured_num_predict",
                "generation_context_token_budget","generation_output_token_reserve",
                "generation_prompt_overhead_tokens","generation_expected_tokens_per_case",
                "generation_expected_output_base_tokens","generation_max_cases_per_model_call",
                "generation_min_cases_per_atom","generation_default_max_cases_per_atom",
                "generation_absolute_max_cases_per_atom","generation_hard_max_cases_per_requirement",
                "generation_max_seconds","generation_model_call_max_seconds",
                "generation_idle_timeout_seconds","generation_batch_max_seconds",
                "generation_json_progress_timeout_seconds","generation_max_output_chars",
                "generation_repetition_guard_enabled","generation_coverage_repair_max_rounds",
                "agent_max_model_calls","agent_max_tool_calls","agent_model_max_retries",
                "agent_tool_max_retries","structured_output_max_retries")})
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
                except GenerationTerminated:
                    run_log.finish("cancelled", failure_reason="explicit_cancel_requested")
                    raise
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
        if request.target_test_points:
            points = {str(x.get("test_point_id") or ""): x for x in request.target_test_points}
            unbound = [case for case in canonical_cases if case.test_point_id not in points]
            unused = [point for point_id, point in points.items() if point_id not in {x.test_point_id for x in canonical_cases}]
            if unbound and len(unbound) == len(unused):
                for case, point in zip(unbound, unused):
                    case.test_point_id = str(point["test_point_id"])
            canonical_cases = [case for case in canonical_cases if case.test_point_id in points]
            if not canonical_cases:
                raise StructuredOutputError("当前批次没有返回可追踪到 target_test_points 的测试用例")
            for case in canonical_cases:
                point = points.get(case.test_point_id)
                if not point:
                    continue
                # Traceability is program-owned. Models may echo these values,
                # but cannot omit or alter the plan identity persisted with a case.
                case.atomic_requirement_id = str(point["atomic_requirement_id"])
                case.test_point_scenario_type = str(point.get("scenario_type") or "other")
                case.indicator_ids = list(dict.fromkeys([*case.indicator_ids, case.atomic_requirement_id]))
                case.generation_batch_id = request.batch_id
            canonical_cases = CaseDeduplicator.deduplicate(canonical_cases)
        # The per-call cap governs atomic TestPoint batches planned by the
        # durable workflow.  Legacy/manual generation requests may explicitly
        # request more cases and must retain their existing behaviour.
        if (request.target_test_points and
                len(canonical_cases) > min(request.case_count, self.settings.generation_max_cases_per_model_call)):
            raise StructuredOutputError("模型返回用例数超过当前安全批次上限")
        rejected_cases: List[Dict[str, Any]] = []
        if mode in ("agent", "model_direct"):
            self._emit(progress_callback,"status","正在执行结构校验")
            canonical_cases, rejected_cases = self._validate_generated_cases(
                canonical_cases, packages, request
            )
            run_log.artifact("05-hard-rejected-cases.json", rejected_cases)
        else:
            canonical_cases, rejected_cases = self._validate_generated_cases(
                canonical_cases, packages, request, render_structured=False
            )
        if not canonical_cases:
            reasons = "；".join(str(item.get("reason") or "") for item in rejected_cases[:5])
            run_log.finish("failed", error="all_generated_cases_hard_rejected", rejected_cases=rejected_cases)
            raise StructuredOutputError(f"全部候选用例均为hard_error，未持久化：{reasons}")
        if cancellation_callback and cancellation_callback():
            run_log.finish("cancelled", failure_reason="client_cancelled")
            raise GenerationTerminated("client_cancelled", "")
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
        valid_count=sum(record.review_status == "ready" for record in records)
        review_count=sum(record.review_status == "draft_needs_review" for record in records)
        run_log.finish("completed", generation_mode=mode, case_count=len(records), valid_case_count=valid_count,
                       review_case_count=review_count, rejected_case_count=len(rejected_cases),
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
            valid_case_count=valid_count,
            review_case_count=review_count,
            rejected_case_count=len(rejected_cases),
            rejected_cases=rejected_cases,
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
        if contexts and request.atomic_requirement_id:
            contexts = [self._scope_atomic_context(item, request.atomic_requirement_id) for item in contexts]
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

    @staticmethod
    def _scope_atomic_context(context: Dict[str, Any], atom_id: str) -> Dict[str, Any]:
        """Keep only evidence relevant to one atomic generation batch."""
        scoped = json.loads(json.dumps(context, ensure_ascii=False, default=str))
        trace = scoped.get("traceability_context") or {}
        trace["atomic_requirements"] = [x for x in trace.get("atomic_requirements") or []
                                          if str(x.get("indicator_id") or "") == atom_id]
        trace["requirement_element_links"] = [x for x in trace.get("requirement_element_links") or []
                                                if str(x.get("indicator_id") or "") == atom_id]
        page_ids = {str(x.get("page_id") or "") for x in trace["requirement_element_links"] if x.get("page_id")}
        page_ids.update(str(x.get("page_id") or "") for x in trace.get("requirement_page_links") or [] if x.get("page_id"))
        trace["playwright_observations"] = [x for x in trace.get("playwright_observations") or []
                                             if not page_ids or str(x.get("page_id") or "") in page_ids]
        evidence = scoped.get("evidence_context") or {}
        evidence["atomic_requirements"] = trace["atomic_requirements"]
        evidence["requirement_element_links"] = trace["requirement_element_links"]
        evidence["playwright_observations"] = trace["playwright_observations"]
        scoped["traceability_context"] = trace
        scoped["evidence_context"] = evidence
        return scoped

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
        model_call_id = "MC-" + new_id("")
        if run_log:
            run_log.artifact("03-model-request.json", {"mode": "direct", "payload": payload, "fingerprints": [item["fingerprint"] for item in packages]})
            run_log.event("MODEL_CALL_STARTED", "Direct model call started", model_call_id=model_call_id,
                          generation_batch_id=request.batch_id, atomic_requirement_id=request.atomic_requirement_id)
        payload["stream"] = bool(progress_callback)
        response = requests.post(
            f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=(self.settings.ollama_timeout, self.settings.generation_idle_timeout_seconds),
            stream=bool(progress_callback),
        )
        if not response.ok:
            logger.error("Generation Ollama HTTP %s response=%s", response.status_code, response_error_text(response))
        raise_for_ollama_status(response)
        if progress_callback:
            data: Dict[str, Any] = {}; first_token=True; last_progress_at=time.monotonic()
            content_parts: List[str] = []
            def events():
                for line in response.iter_lines(decode_unicode=True):
                    if line:
                        event=json.loads(line)
                        thinking=str(((event.get("message") or {}).get("thinking")) or "")
                        if thinking: self._emit(progress_callback,"reasoning",thinking)
                        yield event
            def on_token(part: str) -> None:
                nonlocal first_token, last_progress_at
                last_progress_at=time.monotonic()
                if run_log and first_token:
                    run_log.event("MODEL_FIRST_TOKEN", "first model token", model_call_id=model_call_id)
                    first_token=False
                self._emit(progress_callback, "token", part)
            try:
                data, content = StreamGuard(self.settings, cancellation_callback or (lambda: False)).collect(
                    events(), on_token)
            except GenerationTerminated as exc:
                response.close()
                if run_log:
                    run_log.artifact("04-partial-model-response.json", {"content": exc.partial_output})
                    event_name = "MODEL_CALL_CANCELLED" if exc.reason == "client_cancelled" else "MODEL_CALL_TIMEOUT"
                    run_log.event(event_name, "流式生成已终止", model_call_id=model_call_id,
                                  elapsed_seconds=round(time.monotonic()-last_progress_at,3),
                                  last_progress_at=last_progress_at, termination_reason=exc.reason,
                                  received_chars=len(exc.partial_output))
                if exc.reason == "client_cancelled":
                    raise
                raise StructuredOutputError(f"模型输出已终止：termination_reason={exc.reason}") from exc
            data["message"] = {**(data.get("message") or {}), "content": content}
        else:
            data = response.json()
        if run_log:
            run_log.artifact("04-model-raw-response.json", data)
        content = str(((data.get("message") or {}).get("content")) or "").strip()
        metrics = {key:data.get(key) for key in ("done_reason", "eval_count", "prompt_eval_count")}
        if run_log:
            run_log.event("MODEL_CALL_COMPLETED", "Direct响应统计", model_call_id=model_call_id,
                          received_chars=len(content), num_ctx=self.settings.ollama_num_ctx,
                          num_predict=self.settings.ollama_structured_num_predict, **metrics)
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            truncated = data.get("done_reason") == "length" or (
                int(data.get("eval_count") or 0) >= self.settings.ollama_structured_num_predict
            )
            if truncated:
                if run_log:
                    run_log.event("MODEL_CALL_TRUNCATED", "Direct输出截断", model_call_id=model_call_id,
                                  classification="output_truncated", received_chars=len(content),
                                  num_ctx=self.settings.ollama_num_ctx,
                                  num_predict=self.settings.ollama_structured_num_predict, **metrics)
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

    def _validate_generated_cases(
        self, cases: List[TestCase], packages: List[Dict[str, Any]], request: GenerationRequest,
        *, render_structured: bool = True,
    ) -> tuple[List[TestCase], List[Dict[str, Any]]]:
        """Validate candidates independently; only hard errors reject a case."""
        accepted: List[TestCase] = []
        rejected: List[Dict[str, Any]] = []
        allowed_requirements = set(request.requirement_ids)
        for index, original in enumerate(cases, 1):
            label = f"候选用例{index}《{original.title}》"
            try:
                if not set(original.requirement_ids).issubset(allowed_requirements):
                    raise StructuredOutputError("requirement_id不属于当前请求")
                payload = original.model_dump(mode="json")
                for field_name, value in payload.items():
                    if isinstance(value, str) and len(value) > self.settings.generation_max_field_chars:
                        raise StructuredOutputError(f"字段{field_name}超过GENERATION_MAX_FIELD_CHARS")
                if any(len(step) > self.settings.generation_max_step_chars for step in original.test_steps):
                    raise StructuredOutputError("步骤超过GENERATION_MAX_STEP_CHARS")
                if not original.test_steps or not original.expected_results:
                    raise StructuredOutputError("步骤与预期完全无法配对")
                issues = list(original.quality_issues)
                case = original
                if len(case.test_steps) != len(case.expected_results):
                    pair_count = min(len(case.test_steps), len(case.expected_results))
                    if pair_count <= 0:
                        raise StructuredOutputError("步骤与预期完全无法配对")
                    case = self._repair_step_result_alignment(case)
                    issues.append(self._quality_issue("step_result_alignment_repaired", "步骤与预期数量不一致，已保留可配对部分", "review_required"))
                if render_structured:
                    case = self._validate_and_render_detailed_cases([case], packages, request.case_type)[0]
                issues = list(dict.fromkeys(json.dumps(item,ensure_ascii=False,sort_keys=True) for item in [*issues,*case.quality_issues]))
                normalized_issues = [json.loads(item) for item in issues]
                review = bool(case.need_human_confirm or any(
                    item.get("severity") == "review_required" for item in normalized_issues
                ))
                accepted.append(case.model_copy(update={
                    "quality_issues": normalized_issues,
                    "review_status": "draft_needs_review" if review else "ready",
                    "need_human_confirm": review,
                }))
            except (StructuredOutputError, ValidationError, ValueError, TypeError) as exc:
                rejected.append({
                    "candidate_index": index, "candidate_label": label,
                    "severity": "hard_error", "reason": str(exc),
                    "raw_case": original.model_dump(mode="json"),
                })
        return accepted, rejected

    @staticmethod
    def _quality_issue(code: str, message: str, severity: str = "review_required", **details: Any) -> Dict[str, Any]:
        return {"code":code, "severity":severity, "message":message, **details}

    @staticmethod
    def _concrete_input_value(case: TestCase, step: Any, packages: List[Dict[str, Any]]) -> str:
        name = str(step.element_name or "").strip("〖〗 ")
        values = [str(item).strip() for item in case.test_data if str(item).strip()]
        for wrapped in packages:
            package = wrapped.get("package", {})
            inputs = (package.get("requirement") or {}).get("inputs") or []
            if isinstance(inputs, dict):
                values.extend(f"{key}: {value}" for key,value in inputs.items() if value not in (None,"",[],{}))
            elif isinstance(inputs, list):
                values.extend(str(item).strip() for item in inputs if str(item).strip())
            elif inputs:
                values.append(str(inputs).strip())
        ordered = sorted(values, key=lambda value: (0 if name and name in value else 1, len(value)))
        for value in ordered:
            match = re.search(r"(?:[:：=]|输入|选择|设置|上传)\s*[‘’'\"“”]?([^,，;；\n‘’'\"“”]+)", value)
            candidate = (match.group(1) if match else (value if len(ordered)==1 else "")).strip()
            if (candidate and candidate not in {name,"待确认","未知","按需求填写","具体值"}
                    and not any(marker in candidate for marker in ("需求规定","按需求","待补充","待明确"))):
                return candidate[:500]
        return ""

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
            quality_issues = list(case.quality_issues)
            if not case.preconditions:
                quality_issues.append(GenerationService._quality_issue("incomplete_preconditions", "先决条件不完整"))
            if not case.evaluation_criteria.strip() or case.evaluation_criteria.strip() in {"通过", "符合要求", "正确"}:
                quality_issues.append(GenerationService._quality_issue("generic_pass_criteria", "通过准则不够具体"))
            if valid_pages and not case.structured_steps:
                quality_issues.append(GenerationService._quality_issue("missing_structured_steps", "缺少结构化步骤，保留原始步骤待审核"))
                case = case.model_copy(update={"quality_issues":quality_issues,"need_human_confirm":True})
            if not case.structured_steps:
                rendered.append(case)
                continue
            instructions: List[str] = []
            expected: List[str] = []
            normalized_steps = []
            case_needs_confirmation = case.need_human_confirm
            for index, original_step in enumerate(case.structured_steps, 1):
                step = original_step
                action = str(step.action or "").strip().lower()
                input_actions = {"input","select","set","fill","upload","输入","填写","选择","设置","上传"}
                ui_actions = input_actions | {"click","check","uncheck","点击","勾选","取消勾选"}
                if not step.element_id and action in ui_actions:
                    matches = [
                        element_id
                        for element_id, item in element_evidence.items()
                        if item.get("element_name") and str(item["element_name"]) in step.instruction
                    ]
                    if len(matches) == 1:
                        step = step.model_copy(update={"element_id": matches[0]})
                evidence = element_evidence.get(step.element_id)
                # Explicit IDs are factual claims.  Validate them before the
                # pure-requirement fallback clears descriptive UI fields.
                if step.page_id and step.page_id not in allowed_pages:
                    raise StructuredOutputError(f"使用不存在的证据页面 {step.page_id}")
                if step.element_id and step.element_id not in allowed_elements:
                    raise StructuredOutputError(f"使用不存在的证据元素 {step.element_id}")
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
                if (not allowed_pages or (candidate_mode and not step.page_id and not step.element_id)) and action in ui_actions:
                    step = step.model_copy(update={
                        "page_id":"", "page_name":"", "region":"", "element_id":"",
                        "element_name":"", "element_type":"", "need_human_confirm":True,
                    })
                    case_needs_confirmation = True
                if step.step_no != index:
                    quality_issues.append(GenerationService._quality_issue("step_number_repaired", f"第{index}步编号不连续，已自动修正", "warning"))
                    step = step.model_copy(update={"step_no":index})
                if step.page_id and step.page_id not in allowed_pages:
                    raise StructuredOutputError(f"用例 {case.case_id} 使用不存在的证据页面 {step.page_id}")
                if step.element_id and step.element_id not in allowed_elements:
                    raise StructuredOutputError(f"用例 {case.case_id} 使用不存在的证据元素 {step.element_id}")
                if evidence and step.page_id and str(evidence["page_id"]) != step.page_id:
                    raise StructuredOutputError(f"用例 {case.case_id} 的元素 {step.element_id} 不属于页面 {step.page_id}")
                if action in ui_actions:
                    if valid_pages or (candidate_mode and (step.page_id or step.element_id)):
                        if not step.page_id or not step.page_name or not step.region:
                            quality_issues.append(GenerationService._quality_issue("missing_page_region_detail", f"第{index}步页面或区域不够详细"))
                            step = step.model_copy(update={"need_human_confirm":True})
                            case_needs_confirmation = True
                        if not step.element_id or not evidence:
                            quality_issues.append(GenerationService._quality_issue("missing_real_element", f"第{index}步未选出真实目录元素"))
                            step = step.model_copy(update={"need_human_confirm":True})
                            case_needs_confirmation = True
                        if evidence and not evidence.get("region"):
                            quality_issues.append(GenerationService._quality_issue("position_pending_confirmation", f"第{index}步真实元素缺少方位证据"))
                            step = step.model_copy(update={"need_human_confirm":True})
                            case_needs_confirmation = True
                        if not step.element_name or "〖" not in step.element_name or "〗" not in step.element_name:
                            quality_issues.append(GenerationService._quality_issue("control_name_format", f"第{index}步控件名称格式待修正", "warning"))
                if action in input_actions and not step.input_value:
                    repaired_value = GenerationService._concrete_input_value(case, step, packages)
                    if repaired_value:
                        step = step.model_copy(update={"input_value":repaired_value})
                        quality_issues.append(GenerationService._quality_issue("input_value_repaired", f"第{index}步已从用例或需求输入资料补入具体值", "warning", repaired_value=repaired_value))
                    else:
                        issue=GenerationService._quality_issue("missing_concrete_input", f"第{index}步输入类动作缺少具体值")
                        step = step.model_copy(update={"need_human_confirm":True,"quality_issues":[*step.quality_issues,issue]})
                        quality_issues.append(issue); case_needs_confirmation=True
                if action in {"wait","等待"} and not re.search(r"\d|直到|直至|条件|出现|完成", step.instruction):
                    issue=GenerationService._quality_issue("missing_wait_condition", f"第{index}步缺少等待时限或结束条件")
                    step=step.model_copy(update={"need_human_confirm":True,"quality_issues":[*step.quality_issues,issue]})
                    quality_issues.append(issue); case_needs_confirmation=True
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
                    observable="具体可观察预期待人工确认"
                    issue=GenerationService._quality_issue("missing_observable_expected", f"第{index}步缺少可观察预期")
                    quality_issues.append(issue); case_needs_confirmation=True
                    step=step.model_copy(update={"need_human_confirm":True})
                if any(word in observable for word in ("正常显示", "正确处理")):
                    quality_issues.append(GenerationService._quality_issue("generic_expected_result", f"第{index}步预期结果较笼统"))
                    case_needs_confirmation=True; step=step.model_copy(update={"need_human_confirm":True})
                if action in ui_actions:
                    if step.page_name and step.element_name:
                        location = f"在〖{step.page_name}〗页面"
                        if step.region:
                            location += f"的{step.region}"
                        action_text = {
                            "click": f"点击{step.element_name}",
                            "input": f"在{step.element_name}中输入“{step.input_value}”",
                            "select": f"从{step.element_name}选择“{step.input_value}”",
                            "check": f"勾选{step.element_name}",
                            "uncheck": f"取消勾选{step.element_name}",
                            "set": f"为{step.element_name}设置“{step.input_value}”",
                            "fill": f"在{step.element_name}中填写“{step.input_value}”",
                            "upload": f"向{step.element_name}上传“{step.input_value}”",
                            "点击": f"点击{step.element_name}", "输入":f"在{step.element_name}中输入“{step.input_value}”",
                            "填写":f"在{step.element_name}中填写“{step.input_value}”", "选择":f"从{step.element_name}选择“{step.input_value}”",
                            "设置":f"为{step.element_name}设置“{step.input_value}”", "上传":f"向{step.element_name}上传“{step.input_value}”",
                            "勾选":f"勾选{step.element_name}", "取消勾选":f"取消勾选{step.element_name}",
                        }.get(action, step.instruction)
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
                "quality_issues":quality_issues,
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
                    "atomic_requirement_id": request.atomic_requirement_id,
                    "test_point_id": "exact id from target_test_points",
                    "test_point_scenario_type": "normal/abnormal/boundary/state/constraint/recovery/other",
                    "generation_batch_id": request.batch_id,
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
            f"Atomic requirement id: {request.atomic_requirement_id}\n"
            f"Target test points: {json.dumps(request.target_test_points, ensure_ascii=False)}\n\n"
            "Return exactly one JSON object matching this schema. The top-level object "
            "must contain the key 'cases'. Do not output analysis keys such as user_profile, "
            "scenario_cards, test_objects, input_data_summary, thresholds, or any Chinese "
            "analysis headings at the top level.\n"
            "Rules:\n"
            "- cases length must be no more than Case count.\n"
            + quantity_rule
            +
            "- test_steps and expected_results must have the same length and align by index.\n"
            "- Generate only this bounded batch. Produce coverage for every target_test_point exactly once unless distinct data_variant cases are justified.\n"
            "- Copy atomic_requirement_id, test_point_id, scenario_type and generation_batch_id into every case; never invent a target id.\n"
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
        # Model case IDs are disposable candidate labels.  Requirement scope and
        # step alignment are checked per case later so one hard error cannot
        # discard otherwise reviewable siblings.

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
            validation_issues = list(case.quality_issues)
            quality["validation_issues"] = validation_issues
            quality["severity_counts"] = {
                level: sum(item.get("severity") == level for item in validation_issues)
                for level in ("hard_error","review_required","warning")
            }
            persistence_data["quality_score"] = quality["score"]
            persistence_data["quality_issues"] = [*validation_issues, *[
                self._quality_issue("quality_standard", str(issue), "review_required")
                for issue in quality["issues"]
            ]]
            persistence_data["review_status"] = case.review_status
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
                    review_status=case.review_status,
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
            if request.atomic_requirement_id:
                atom_ids = [item for item in atom_ids if item == request.atomic_requirement_id]
            valid_atoms = set(atom_ids)
            covered: set[str] = set()
            for case in requirement_cases:
                case.indicator_ids = [
                    item for item in dict.fromkeys(case.indicator_ids) if item in valid_atoms
                ]
                covered.update(case.indicator_ids)
            # Atomic batches have an explicit target. Legacy mode keeps its historical
            # compatibility fill, but coverage completion is never inferred from it.
            if not request.target_test_points:
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
