"""Unified project test-case generation, validation, quality, and persistence service."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agents.test_case.agent import TestCaseAgent
from agents.test_case.context import AgentRuntimeContext
from agents.test_case.output_schema import GeneratedCaseBundle, TestCaseAgentRequest
from config.settings import Settings, settings as default_settings
from core.case_quality_evaluator import evaluate_case_quality
from core.context_builder import ContextBuilder
from domain.exceptions import (
    AgentCallLimitError,
    AgentExecutionError,
    ModelUnavailableError,
    PersistenceError,
    ProjectScopeError,
    StructuredOutputError,
)
from domain.schemas.test_case import TestCase
from infrastructure.llm.ollama_health import OllamaHealthClient
from services.fallback_generation_service import FallbackGenerationService


GenerationMode = Literal["agent", "rule_fallback", "manual"]


class GenerationRequest(BaseModel):
    """Validated input for one project-scoped generation run."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    requirement_ids: List[str] = Field(min_length=1)
    scenario_ids: List[str] = Field(default_factory=list)
    case_type: str = "功能测试"
    case_count: int = Field(default=1, ge=1, le=20)
    requested_mode: Literal["auto", "agent", "rule", "manual"] = "auto"
    use_project_kb: bool = True
    use_history: bool = True
    top_k_chunks: int = Field(default=5, ge=1, le=50)
    top_k_history: int = Field(default=5, ge=0, le=50)
    additional_instructions: str = ""
    manual_cases: List[TestCase] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_manual_mode(self) -> "GenerationRequest":
        if self.requested_mode == "manual" and not self.manual_cases:
            raise ValueError("manual mode requires manual_cases")
        if self.requested_mode != "manual" and self.manual_cases:
            raise ValueError("manual_cases are only accepted in manual mode")
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


AgentBuilder = Callable[[AgentRuntimeContext], Any]


class GenerationService:
    """Single project-scoped generation entry point with explicit fallback semantics."""

    def __init__(
        self,
        manager: Any,
        case_library: Optional[Any] = None,
        settings: Settings = default_settings,
        health_client: Optional[OllamaHealthClient] = None,
        agent_builder: Optional[AgentBuilder] = None,
        fallback_service: Optional[FallbackGenerationService] = None,
    ) -> None:
        self.manager = manager
        self.case_library = case_library
        self.settings = settings
        self.health_client = health_client or OllamaHealthClient(settings)
        self.agent_builder = agent_builder or (lambda runtime: TestCaseAgent(runtime))
        self.fallback_service = fallback_service or FallbackGenerationService()
        self.context_builder = ContextBuilder(manager, case_library)

    def generate_test_cases(self, request: GenerationRequest) -> GenerationResult:
        """Validate, generate, score, persist, and return one explicit-mode result."""
        self._validate_scope(request)
        contexts = self._build_contexts(request)
        mode: GenerationMode
        fallback_reason = ""
        warnings: List[str] = []
        used_tool_names: List[str] = []
        retrieved_chunk_ids: List[str] = []
        overall_missing: List[str] = []

        if request.requested_mode == "manual":
            mode = "manual"
            canonical_cases = [
                case.model_copy(update={"generation_mode": "manual"})
                for case in request.manual_cases
            ]
        else:
            fallback_reason = self._preflight_fallback_reason(request)
            if fallback_reason:
                mode = "rule_fallback"
                canonical_cases = self.fallback_service.generate(
                    contexts, request.case_count, request.case_type, fallback_reason
                )
            else:
                try:
                    runtime = AgentRuntimeContext(
                        project_id=request.project_id,
                        manager=self.manager,
                        case_library=self.case_library if request.use_history else None,
                        settings=self.settings,
                    )
                    bundle = self.agent_builder(runtime).generate(
                        TestCaseAgentRequest(
                            requirement_ids=request.requirement_ids,
                            case_count=request.case_count,
                            case_type=request.case_type,
                            additional_instructions=request.additional_instructions,
                        )
                    )
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
                except (
                    AgentCallLimitError,
                    AgentExecutionError,
                    ModelUnavailableError,
                    StructuredOutputError,
                    ValidationError,
                    TimeoutError,
                ) as exc:
                    mode = "rule_fallback"
                    fallback_reason = f"{type(exc).__name__}: {exc}"
                    warnings.append("Agent 生成失败，已执行确定性规则 fallback。")
                    canonical_cases = self.fallback_service.generate(
                        contexts, request.case_count, request.case_type, fallback_reason
                    )

        if not canonical_cases:
            raise StructuredOutputError("Generation produced no test cases")
        run_id = self.manager.create_generation_run(
            project_id=request.project_id,
            run_type=f"test_case_{mode}",
            model_name=self.settings.ollama_model if mode == "agent" else "",
            prompt_snapshot=json.dumps(
                request.model_dump(mode="json"), ensure_ascii=False
            )[:12000],
            status="completed",
        )
        records = self._score_and_persist(
            request, contexts, canonical_cases, run_id, mode, fallback_reason
        )
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
        return [
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
    ) -> List[GeneratedCaseRecord]:
        context_by_requirement = {
            str(context.get("requirement_id") or ""): context for context in contexts
        }
        records: List[GeneratedCaseRecord] = []
        for case in cases:
            requirement_id = (
                case.requirement_ids[0]
                if case.requirement_ids
                else request.requirement_ids[0]
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
    def _to_persistence_data(
        case: TestCase,
        context: Dict[str, Any],
        project_id: str,
        requirement_id: str,
        case_type: str,
        run_id: str,
        mode: GenerationMode,
        fallback_reason: str,
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
