"""Canonical, bounded model input shared by Agent and direct generation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from domain.rules.standard_knowledge import standard_summary
from domain.rules.evidence_policy import EvidencePolicy
from config.settings import Settings, settings as default_settings


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


def estimate_generation_capacity(*, input_tokens: int, case_count: int, num_ctx: int,
                                 num_predict: int, settings: Settings = default_settings) -> dict[str, Any]:
    expected = settings.generation_expected_output_base_tokens + max(1, int(case_count)) * settings.generation_expected_tokens_per_case
    available = max(0, min(int(num_predict), int(num_ctx) - int(input_tokens)))
    return {"input_tokens":int(input_tokens), "expected_output_tokens":expected,
            "available_output_tokens":available, "case_count":int(case_count),
            "capacity_sufficient":expected <= available}


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
    case_count: int = 1,
    case_type: str = "功能测试",
    settings: Settings = default_settings,
) -> dict[str, Any]:
    """Project-scoped whitelist projection with evidence-priority trimming."""
    requirement = dict(context.get("requirement") or {})
    trace = context.get("traceability_context") or {}
    page_links = list(trace.get("requirement_page_links") or [])
    element_links = list(trace.get("requirement_element_links") or [])
    page_ids = [str(row.get("page_id")) for row in page_links if row.get("page_id")]
    policy = EvidencePolicy.for_test_type(case_type, settings)
    element_ids = [str(row.get("confirmed_element_id")) for row in element_links if row.get("confirmed_element_id")]
    pending_page_ids = [str(row.get("page_id")) for row in page_links if row.get("page_id") and row.get("status") == "page_confirmed_element_pending"]
    pages: dict[str, dict[str, Any]] = {}
    elements: dict[str, dict[str, Any]] = {}
    has_project_html = False
    with manager.connections.connection() as conn:
        has_project_html = bool(conn.execute(
            "SELECT 1 FROM html_pages WHERE project_id=? LIMIT 1", (context.get("project_id"),)
        ).fetchone())
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
                        "options", "required", "readonly", "disabled", "region", "position", "semantic_position",
                    ) if detail.get(key) not in (None, "", [], {})})
                    elements[element_id] = item
            if policy.include_elements:
                for page_id in pending_page_ids:
                    rows = conn.execute(
                        "SELECT element_id,page_id,tag,element_type,element_json FROM html_elements WHERE project_id=? AND page_id=? ORDER BY element_id",
                        (context.get("project_id"), page_id),
                    ).fetchall()
                    seen: set[tuple[str, str, str]] = set()
                    for row in rows:
                        item = dict(row); detail = json.loads(item.pop("element_json") or "{}")
                        name = str(detail.get("label") or detail.get("text") or detail.get("name") or detail.get("placeholder") or "").strip()
                        if not name or detail.get("visible") is False or detail.get("type") == "hidden":
                            continue
                        key = (name, str(item.get("element_type") or item.get("tag") or ""), str(detail.get("region") or ""))
                        if key in seen: continue
                        seen.add(key)
                        elements[str(item["element_id"])] = _clean({**item, "name": name[:settings.html_max_element_text_chars],
                            "region": detail.get("region"), "relative_position": detail.get("relative_position"),
                            "position_phrase": detail.get("position_phrase"), "position_source": detail.get("position_source"),
                            "visible": detail.get("visible", True), "enabled": not detail.get("disabled", False),
                            "required": detail.get("required", False), "readonly": detail.get("readonly", False),
                            "default_value": str(detail.get("value") or "")[:settings.html_max_element_text_chars],
                            "options": list(detail.get("options") or [])[:100]})
                        if len([x for x in elements.values() if x.get("page_id") == page_id]) >= settings.html_max_elements_per_page:
                            break
    page_evidence = []
    for link in page_links:
        page_id = str(link.get("page_id") or "")
        linked_elements = [
            {**elements.get(str(item.get("confirmed_element_id") or ""), {}),
             "binding_status": item.get("status"), "confidence": item.get("confidence")}
            for item in element_links if str(item.get("page_id") or "") == page_id
            and item.get("confirmed_element_id")
        ]
        if page_id in pending_page_ids:
            linked_elements = [{**item, "binding_status": "selectable_on_generation"}
                               for item in elements.values() if str(item.get("page_id")) == page_id]
        page_evidence.append(_clean({
            **pages.get(page_id, {"page_id": page_id}),
            "binding_status": link.get("status"),
            "confidence": link.get("confidence"),
            "reason": link.get("reason"),
            "need_human_confirm": link.get("need_human_confirm"),
            "elements": linked_elements,
            "playwright_observations": ([
                item for item in trace.get("playwright_observations") or []
                if str(item.get("page_id") or "") == page_id
            ][:settings.generation_max_page_observations] if policy.include_observations else []),
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
            "evidence_policy": policy.as_dict(),
        },
        "missing_information": context.get("missing_information") or [],
        "traceability": {
            "page_bindings": page_links,
            "element_bindings": element_links,
        },
        "history_writing_examples": (context.get("similar_library_cases") or [])[:settings.generation_max_history_examples],
        "source_excerpt": [
            {"chunk_id": row.get("chunk_id"), "source_document": row.get("filename") or row.get("source_document"), "content": str(row.get("content") or row.get("chunk_text") or "")[:settings.generation_max_related_chunk_chars]}
            for row in (context.get("related_chunks") or [])[:settings.generation_max_related_chunks]
        ],
    })
    if not policy.include_elements:
        package["page_evidence"] = [{k:v for k,v in page.items() if k not in {"elements", "forms", "tables", "dialogs"}}
                                    for page in package.get("page_evidence", [])]
    before_tokens = estimate_tokens(package)
    configured = min(settings.generation_context_token_budget,
                     int(num_ctx * settings.generation_context_safety_ratio) - settings.generation_output_token_reserve)
    budget = max(1024, configured)
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
            if len(observations) > settings.generation_max_page_observations:
                page["playwright_observations"] = observations[:settings.generation_max_page_observations]
                trimmed.append(f"page_evidence.{page.get('page_id')}.low_priority_observations")
    after_tokens = estimate_tokens(package)
    input_tokens = after_tokens + settings.generation_prompt_overhead_tokens
    capacity = estimate_generation_capacity(input_tokens=input_tokens, case_count=case_count,
        num_ctx=num_ctx, num_predict=num_predict, settings=settings)
    canonical = json.dumps(package, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "package": package,
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "token_estimate_before": before_tokens,
        "token_estimate_after": after_tokens,
        "token_budget": budget,
        "trimmed_fields": trimmed,
        "may_exceed_context": after_tokens + num_predict > num_ctx,
        **capacity,
        "has_project_html": has_project_html,
    }
