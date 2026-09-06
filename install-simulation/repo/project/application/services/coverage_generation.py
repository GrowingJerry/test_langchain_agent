"""Program-owned batching, reconciliation and de-duplication for atomic coverage."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import requests

from config.settings import Settings
from domain.schemas.coverage import CoverageAudit, ScenarioType, TestPoint
from domain.schemas.test_case import TestCase


@dataclass(frozen=True)
class GenerationBatch:
    batch_id: str
    atomic_requirement_id: str
    test_points: tuple[TestPoint, ...]


class GenerationBatchPlanner:
    """Create token-safe batches; never applies a normal requirement-wide cap."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def plan(self, requirement_id: str, points: Iterable[TestPoint], available_output_tokens: int | None = None) -> list[GenerationBatch]:
        points = list(points)
        token_limit = self.settings.generation_max_cases_per_model_call
        if available_output_tokens is not None:
            usable = max(0, available_output_tokens - self.settings.generation_expected_output_base_tokens)
            token_limit = max(1, usable // self.settings.generation_expected_tokens_per_case)
        limit = max(1, min(token_limit, self.settings.generation_max_cases_per_model_call))
        result: list[GenerationBatch] = []
        by_atom: dict[str, list[TestPoint]] = {}
        for point in points:
            by_atom.setdefault(point.atomic_requirement_id, []).append(point)
        for atom_id, atom_points in by_atom.items():
            for offset in range(0, len(atom_points), limit):
                chunk = tuple(atom_points[offset:offset + limit])
                digest = hashlib.sha256("|".join(x.test_point_id for x in chunk).encode()).hexdigest()[:12]
                result.append(GenerationBatch(f"GB-{requirement_id}-{digest}", atom_id, chunk))
        return result


class CoveragePlanner:
    """Ask the model for independent test purposes, never for full test cases."""

    def __init__(self, settings: Settings, session: Any = requests):
        self.settings, self.session = settings, session

    def plan_atom(self, *, requirement_id: str, atom: dict[str, Any], evidence: dict[str, Any],
                  coverage_types: Iterable[str], max_cases: int) -> list[TestPoint]:
        atom_id = str(atom.get("indicator_id") or atom.get("atomic_requirement_id") or "")
        schema = {"type": "object", "required": ["test_points"], "properties": {
            "test_points": {"type": "array", "maxItems": min(max_cases, self.settings.generation_absolute_max_cases_per_atom),
                "items": {"type": "object", "required": ["title", "scenario_type", "description", "priority", "evidence_requirement"],
                    "properties": {"title": {"type": "string"}, "scenario_type": {"type": "string", "enum": list(ScenarioType.__args__)},
                        "description": {"type": "string"}, "priority": {"type": "string"}, "evidence_requirement": {"type": "string"}}}}}}
        prompt = """识别为了充分验证当前原子需求需要覆盖哪些具有独立测试价值的场景，只规划测试点，不生成测试步骤。\n
数量是场景分析结果，不是凑数目标。优先正常路径，并按语义识别合理异常、输入边界、前置状态、业务约束和适用的恢复路径。
禁止重复或无意义测试点。HTML证据充足时结合真实页面元素；HTML不支持的性能、安全、可靠性测试不得虚构UI操作。
只输出符合 schema 的 JSON。\n""" + json.dumps({"requirement_id": requirement_id, "atomic_requirement": atom,
            "enabled_coverage_types": list(coverage_types), "maximum_test_points": max_cases,
            "relevant_html_evidence": evidence}, ensure_ascii=False)
        response = self.session.post(self.settings.ollama_base_url.rstrip("/") + "/api/chat", json={
            "model": self.settings.test_case_model, "stream": False, "think": False, "format": schema,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0, "num_ctx": self.settings.ollama_num_ctx,
                        "num_predict": min(self.settings.ollama_structured_num_predict, 4096)}},
            timeout=(self.settings.ollama_timeout, self.settings.generation_idle_timeout_seconds))
        response.raise_for_status()
        raw = json.loads(response.json()["message"]["content"])["test_points"]
        points = []
        for index, item in enumerate(raw, 1):
            points.append(TestPoint(test_point_id=f"TP-{atom_id}-{index:03d}", atomic_requirement_id=atom_id,
                requirement_id=requirement_id, **item))
        return points


class CoverageReconciler:
    @staticmethod
    def audit(points: Iterable[TestPoint], cases: Iterable[TestCase], repair_rounds_exhausted: bool = False) -> CoverageAudit:
        planned = list(dict.fromkeys(x.test_point_id for x in points))
        generated = list(dict.fromkeys(x.test_point_id for x in cases if x.test_point_id))
        missing = [x for x in planned if x not in set(generated)]
        status = "completed" if not missing else ("needs_review" if repair_rounds_exhausted else "incomplete_coverage")
        return CoverageAudit(planned_test_point_ids=planned, generated_test_point_ids=generated,
                             missing_test_point_ids=missing, coverage_status=status)


class CaseDeduplicator:
    """Keep the most complete case for a duplicate semantic variant."""

    @staticmethod
    def _normalized(value: object) -> str:
        return re.sub(r"\W+", "", json.dumps(value, ensure_ascii=False, sort_keys=True)).lower()

    @classmethod
    def key(cls, case: TestCase) -> tuple[str, ...]:
        variant = case.data_variant or case.scenario_variant
        if case.test_point_id:
            # A plan point is the primary semantic identity. Multiple data cases
            # survive only when the model names a distinct variant explicitly.
            return (case.atomic_requirement_id, case.test_point_id, variant)
        return (case.atomic_requirement_id, "", variant, cls._normalized(case.objective),
                cls._normalized(case.test_steps), cls._normalized(case.expected_results))

    @staticmethod
    def score(case: TestCase) -> int:
        return sum(len(str(x)) for x in [case.title, case.objective, *case.test_steps, *case.expected_results]) + len(case.structured_steps) * 50

    @classmethod
    def deduplicate(cls, cases: Iterable[TestCase]) -> list[TestCase]:
        selected: dict[tuple[str, ...], TestCase] = {}
        for case in cases:
            key = cls.key(case)
            if key not in selected or cls.score(case) > cls.score(selected[key]):
                selected[key] = case
        return list(selected.values())
