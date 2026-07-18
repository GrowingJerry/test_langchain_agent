"""Read-only review orchestration; suggestions never mutate generated cases."""

import json
import re
from typing import Any, Dict, List, Optional

from chains.test_case_review import TestCaseReviewChain
from core.case_quality_evaluator import evaluate_case_quality
from domain.schemas.review import Review


class ReviewService:
    def __init__(
        self, manager: Any, llm_chain: Optional[TestCaseReviewChain] = None
    ) -> None:
        self.manager = manager
        self.llm_chain = llm_chain

    def deterministic_review(self, case_id: str, case: Dict[str, Any]) -> Review:
        issues: List[str] = []
        for fields, label in (
            (("objective", "test_purpose"), "测试目的"),
            (("test_steps",), "测试步骤"),
            (("expected_results", "expected_result"), "预期结果"),
            (("evaluation_criteria", "pass_criteria"), "判定准则"),
        ):
            if not any(case.get(field) for field in fields):
                issues.append(f"缺少{label}")
        text = json.dumps(case, ensure_ascii=False)
        if any(
            word in text for word in ("性能", "响应", "并发", "吞吐", "时延")
        ) and not re.search(r"\d", text):
            issues.append("缺少明确判定阈值，需人工确认")
        if case.get("need_human_confirm") or case.get("need_human_confirmation"):
            issues.append("用例已标记为需人工确认")
        return Review(
            artifact_id=case_id,
            review_type="deterministic",
            status="needs_revision" if issues else "passed",
            issues=issues,
            suggestions=list(issues),
            need_human_confirm=bool(issues),
        )

    def quality_review(
        self,
        case_id: str,
        case: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Review:
        result = evaluate_case_quality(case, context or {})
        return Review(
            artifact_id=case_id,
            review_type="quality",
            status="needs_revision" if result.get("issues") else "passed",
            score=float(result.get("score") or 0),
            issues=result.get("issues") or [],
            suggestions=result.get("suggestions") or [],
            need_human_confirm=bool(result.get("issues")),
        )

    def llm_review(self, case_id: str, case: Dict[str, Any]) -> Review:
        if self.llm_chain is None:
            raise RuntimeError("LLM structured review is not configured")
        return self.llm_chain.run(case_id, case)

    def review_project(
        self, project_id: str, include_llm: bool = False
    ) -> List[Review]:
        results = []
        for row in self.manager.list_generated_cases(project_id):
            case_id = row["case_id"]
            case = row.get("case_json") or {}
            reviews = [
                self.deterministic_review(case_id, case),
                self.quality_review(case_id, case),
            ]
            if include_llm:
                reviews.append(self.llm_review(case_id, case))
            for review in reviews:
                self.manager.save_review_result(
                    project_id,
                    case_id,
                    review.review_type,
                    review.status,
                    review.issues,
                )
            results.extend(reviews)
        return results

    def confirm_update(
        self,
        project_id: str,
        case_id: str,
        updated_case: Dict[str, Any],
        generation_run_id: str = "",
    ) -> None:
        payload = dict(updated_case)
        payload["case_id"] = case_id
        self.manager.save_generated_case(project_id, payload, generation_run_id, None)
