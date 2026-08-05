"""Unified project test-case generation, validation, quality, and persistence service."""

from __future__ import annotations

import json
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


GenerationMode = Literal["agent", "model_direct", "rule_fallback", "manual"]


class GenerationRequest(BaseModel):
    """Validated input for one project-scoped generation run."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=1)
    requirement_ids: List[str] = Field(default_factory=list)
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
    recommended_test_type: str = ""
    selected_test_type: str = ""
    test_type_overridden: bool = False
    test_type_confidence: float = 0.0
    test_type_reasons: List[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_manual_mode(self) -> "GenerationRequest":
        if self.requested_mode == "manual" and not self.manual_cases:
            raise ValueError("manual mode requires manual_cases")
        if self.requested_mode != "manual" and self.manual_cases:
            raise ValueError("manual_cases are only accepted in manual mode")
        if not self.requirement_ids and not self.scenario_ids:
            raise ValueError("requirement_ids or scenario_ids is required")
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
                            scenario_ids=request.scenario_ids,
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
                    ConfigurationError,
                    ModelUnavailableError,
                    StructuredOutputError,
                    ValidationError,
                    TimeoutError,
                ) as exc:
                    agent_failure = f"{type(exc).__name__}: {exc}"
                    try:
                        canonical_cases = self._generate_with_direct_model(
                            contexts, request, agent_failure
                        )
                        mode = "model_direct"
                        fallback_reason = ""
                        warnings.append(
                            f"LangChain Agent 失败，已改用同一 Ollama 模型直接生成：{agent_failure}"
                        )
                    except Exception as direct_exc:
                        mode = "rule_fallback"
                        fallback_reason = (
                            f"{agent_failure}; direct_model_failed="
                            f"{type(direct_exc).__name__}: {direct_exc}"
                        )
                        warnings.append(
                            "Agent 和直连大模型生成均失败，已执行确定性规则 fallback。"
                        )
                        canonical_cases = self.fallback_service.generate(
                            contexts, request.case_count, request.case_type, fallback_reason
                        )

        if not canonical_cases:
            raise StructuredOutputError("Generation produced no test cases")
        metadata = self._generation_metadata(request)
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
        }

    def _generate_with_direct_model(
        self,
        contexts: List[Dict[str, Any]],
        request: GenerationRequest,
        agent_failure: str,
    ) -> List[TestCase]:
        if self.direct_model_generator is not None:
            return self._prepare_model_direct_cases(
                self.direct_model_generator(contexts, request, agent_failure),
                request,
            )
        payload = self._direct_model_payload(contexts, request, agent_failure)
        response = requests.post(
            f"{self.settings.ollama_base_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=self.settings.ollama_timeout,
        )
        response.raise_for_status()
        data = response.json()
        content = str(((data.get("message") or {}).get("content")) or "").strip()
        parsed = json.loads(content)
        try:
            bundle = GeneratedCaseBundle.model_validate(parsed)
            cases = bundle.cases
        except ValidationError:
            cases = self._coerce_direct_model_json_to_cases(parsed, contexts, request)
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
            str(requirement.get("title") or "").strip()
            or str(parsed.get("title") or parsed.get("测试用例标题") or "").strip()
            or f"{request.case_type} for {requirement_id}"
        )
        description = str(
            requirement.get("description")
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
            for row in context.get("related_chunks") or []
            if row.get("chunk_id")
        ]
        source_documents = list(
            dict.fromkeys(
                str(row.get("filename") or row.get("source_document") or "")
                for row in context.get("related_chunks") or []
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
        contexts: List[Dict[str, Any]],
        request: GenerationRequest,
        agent_failure: str,
    ) -> Dict[str, Any]:
        compact_contexts = []
        for context in contexts:
            requirement = dict(context.get("requirement") or {})
            scenarios = list(context.get("related_scenario_cards") or [])
            chunks = [
                {
                    "chunk_id": row.get("chunk_id"),
                    "filename": row.get("filename"),
                    "content": str(row.get("content") or row.get("chunk_text") or "")[:1200],
                }
                for row in context.get("related_chunks") or []
            ][:5]
            compact_contexts.append({
                "requirement_id": context.get("requirement_id"),
                "requirement": requirement,
                "related_scenario_cards": scenarios[:3],
                "related_chunks": chunks,
                "missing_information": context.get("missing_information") or [],
            })
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
                }
            ],
            "overall_missing_information": [],
            "used_tool_names": [],
            "retrieved_source_chunk_ids": [],
            "knowledge_unit_ids": [],
            "equipment_ids": [],
            "configuration_rule_ids": [],
            "scenario_validation_run_id": "",
            "warnings": [],
        }
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
            + json.dumps(compact_contexts, ensure_ascii=False, default=str)[:24000]
        )
        prompt = (
            "You are a test-case generation model. The LangChain Agent failed, "
            "but you must still generate test cases only from the supplied project context.\n"
            f"Agent failure: {agent_failure}\n"
            f"Target requirement_ids: {request.requirement_ids}\n"
            f"Target scenario_ids: {request.scenario_ids}\n"
            f"Selected test type: {request.case_type}\n"
            f"Case count: {request.case_count}\n\n"
            "Return exactly one JSON object matching this schema. The top-level object "
            "must contain the key 'cases'. Do not output analysis keys such as user_profile, "
            "scenario_cards, test_objects, input_data_summary, thresholds, or any Chinese "
            "analysis headings at the top level.\n"
            "Rules:\n"
            "- cases length must be no more than Case count.\n"
            "- test_steps and expected_results must have the same length and align by index.\n"
            "- requirement_ids may only use Target requirement_ids.\n"
            "- source_chunk_ids may only use chunk_id values present in project_context.\n"
            "- Generate cases for the selected test type, not the recommended type if different.\n"
            "- Output JSON only. No markdown. No explanation.\n\n"
            "Required JSON schema example:\n"
            + json.dumps(schema_hint, ensure_ascii=False)
            + "\n\nproject_context:\n"
            + json.dumps(compact_contexts, ensure_ascii=False, default=str)[:24000]
        )
        return {
            "model": self.settings.ollama_model,
            "messages": [
                {"role": "system", "content": "只输出满足用户给定结构的 JSON。"},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "format": "json",
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
