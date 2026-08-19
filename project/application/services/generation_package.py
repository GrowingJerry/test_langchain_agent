"""Canonical, bounded model input shared by Agent and direct generation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from domain.rules.standard_knowledge import standard_summary


WRITING_RULES = [
    "正常、异常和边界用例独立；每个用例验证明确功能点。",
    "先决条件必须给出可复现的完整页面导航路径。",
    "每一步只能有一个动作，并与同序号预期结果一一对应。",
    "页面控件名称使用〖〗包裹，并写明页面、区域或相对方位。",
    "输入、选择必须使用具体值；等待必须写明最长时间或结束条件。",
    "预期结果必须具体可观察，禁止使用“正常显示”“正确处理”。",
    "离线证据不能证明服务端或数据库成功，相关结果标记待联机确认。",
    "没有已绑定页面或元素证据时不得编造控件、位置或页面行为。",
]


def estimate_tokens(value: Any) -> int:
    """Conservative local estimate suitable for Chinese/JSON prompt budgeting."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return max(1, (len(text.encode("utf-8")) + 2) // 3)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        removed = {
            "created_at", "updated_at", "project_id", "batch_id", "generation_run_id",
            "context_id", "quality_score", "quality_issues", "review_changes",
            "machine_extraction", "browser_version", "provenance",
        }
        result = {key: _clean(item) for key, item in value.items() if key not in removed and not key.endswith("_json")}
        return {key: item for key, item in result.items() if item not in (None, "", [], {})}
    if isinstance(value, list):
        result = [_clean(item) for item in value]
        return [item for item in result if item not in (None, "", [], {})]
    if isinstance(value, str):
        lowered = value.lower()
        if "base64," in lowered or len(value) > 12000:
            return value[:12000] + "…[bounded]"
    return value


def build_generation_package(
    manager: Any,
    context: dict[str, Any],
    *,
    num_ctx: int,
    num_predict: int,
) -> dict[str, Any]:
    """Project-scoped whitelist projection with evidence-priority trimming."""
    requirement = dict(context.get("requirement") or {})
    trace = context.get("traceability_context") or {}
    page_links = list(trace.get("requirement_page_links") or [])
    element_links = list(trace.get("requirement_element_links") or [])
    page_ids = [str(row.get("page_id")) for row in page_links if row.get("page_id")]
    element_ids = [str(row.get("confirmed_element_id")) for row in element_links if row.get("confirmed_element_id")]
    pages: dict[str, dict[str, Any]] = {}
    elements: dict[str, dict[str, Any]] = {}
    if page_ids or element_ids:
        with manager.connections.connection() as conn:
            for page_id in dict.fromkeys(page_ids):
                row = conn.execute(
                    "SELECT page_id,title,page_path FROM html_pages WHERE project_id=? AND page_id=?",
                    (context.get("project_id"), page_id),
                ).fetchone()
                if row:
                    pages[page_id] = dict(row)
            for element_id in dict.fromkeys(element_ids):
                row = conn.execute(
                    "SELECT element_id,page_id,tag,element_type,element_json FROM html_elements WHERE project_id=? AND element_id=?",
                    (context.get("project_id"), element_id),
                ).fetchone()
                if row:
                    item = dict(row)
                    try:
                        detail = json.loads(item.pop("element_json") or "{}")
                    except (TypeError, json.JSONDecodeError):
                        detail = {}
                    item.update({key: detail.get(key) for key in (
                        "text", "label", "placeholder", "role", "name", "value",
                        "options", "required", "readonly", "disabled", "region", "position",
                    ) if detail.get(key) not in (None, "", [], {})})
                    elements[element_id] = item
    page_evidence = []
    for link in page_links:
        page_id = str(link.get("page_id") or "")
        linked_elements = [
            {**elements.get(str(item.get("confirmed_element_id") or ""), {}),
             "binding_status": item.get("status"), "confidence": item.get("confidence")}
            for item in element_links if str(item.get("page_id") or "") == page_id
            and item.get("confirmed_element_id")
        ]
        page_evidence.append(_clean({
            **pages.get(page_id, {"page_id": page_id}),
            "binding_status": link.get("status"),
            "confidence": link.get("confidence"),
            "reason": link.get("reason"),
            "need_human_confirm": link.get("need_human_confirm"),
            "elements": linked_elements,
            "playwright_observations": [
                item for item in trace.get("playwright_observations") or []
                if str(item.get("page_id") or "") == page_id
            ][:20],
        }))
    package = _clean({
        "requirement": {
            "requirement_id": requirement.get("requirement_id") or context.get("requirement_id"),
            "name": requirement.get("title"),
            "hierarchy_path": requirement.get("section_path") or requirement.get("hierarchy_path"),
            "functional_description": requirement.get("description"),
            "inputs": requirement.get("inputs"),
            "processing": requirement.get("processing_rules"),
            "outputs": requirement.get("outputs"),
            "constraints": requirement.get("constraints"),
            "source_document": requirement.get("source_document"),
        },
        "atomic_requirements": trace.get("atomic_requirements") or [],
        "page_evidence": page_evidence,
        "writing_standard": {"source": "军用软件测试用例书写标准.docx", "summary": standard_summary(), "mandatory_rules": WRITING_RULES},
        "generation_requirements": {
            "one_requirement_per_batch": True,
            "structured_steps": True,
            "coverage_plan_required": True,
            "evidence_policy": trace.get("evidence_policy"),
        },
        "missing_information": context.get("missing_information") or [],
        "traceability": {
            "page_bindings": page_links,
            "element_bindings": element_links,
        },
        "history_writing_examples": (context.get("similar_library_cases") or [])[:2],
        "source_excerpt": [
            {"chunk_id": row.get("chunk_id"), "source_document": row.get("filename") or row.get("source_document"), "content": str(row.get("content") or row.get("chunk_text") or "")[:1200]}
            for row in (context.get("related_chunks") or [])[:3]
        ],
    })
    before_tokens = estimate_tokens(package)
    budget = max(2048, num_ctx - num_predict - 1500)
    trimmed: list[str] = []
    if before_tokens > budget:
        for field in ("history_writing_examples", "source_excerpt"):
            if package.pop(field, None):
                trimmed.append(field)
            if estimate_tokens(package) <= budget:
                break
    if estimate_tokens(package) > budget:
        for page in package.get("page_evidence", []):
            observations = page.get("playwright_observations") or []
            if len(observations) > 5:
                page["playwright_observations"] = observations[:5]
                trimmed.append(f"page_evidence.{page.get('page_id')}.low_priority_observations")
    after_tokens = estimate_tokens(package)
    canonical = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "package": package,
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "token_estimate_before": before_tokens,
        "token_estimate_after": after_tokens,
        "token_budget": budget,
        "trimmed_fields": trimmed,
        "may_exceed_context": after_tokens + num_predict > num_ctx,
    }
