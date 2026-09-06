"""Rule-based quality evaluation for grounded scenario test cases."""

from __future__ import annotations

import json
import re
from typing import Any, Dict


def _as_list(value: Any) -> list:
    """Normalize mixed string/list values to a list of strings."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[,，;；\s]+", value) if x.strip()]
    return [str(value).strip()]


def _visual_evidence_usage(
    case: Dict[str, Any], context: Dict[str, Any], case_text: str
) -> Dict[str, Any]:
    """Evaluate whether visual evidence is used as safe auxiliary evidence."""
    context_evidence = context.get("visual_evidence") or []
    evidence_by_id = {
        str(item.get("evidence_id")): item
        for item in context_evidence
        if item.get("evidence_id")
    }
    evidence_sources = _as_list(
        case.get("evidence_sources") or case.get("trace_sources")
    )
    if not context_evidence and not evidence_sources:
        return {
            "score": None,
            "status": "not_applicable",
            "issues": [],
            "suggestions": [],
            "reasons": ["当前上下文和用例均未使用视觉证据，不参与扣分。"],
        }

    issues = []
    suggestions = []
    reasons = []
    score = 85.0

    visual_words = (
        "界面",
        "按钮",
        "控件",
        "流程图",
        "节点",
        "截图",
        "状态提示",
        "告警区域",
    )
    mentions_visual = any(word in case_text for word in visual_words)
    if mentions_visual and not evidence_sources:
        score -= 35
        issues.append("用例描述使用了视觉/界面信息，但未标记 evidence_id")
        suggestions.append("在 evidence_sources 中补充使用到的视觉证据 evidence_id")

    unknown_ids = [eid for eid in evidence_sources if eid not in evidence_by_id]
    if unknown_ids:
        score -= 30
        issues.append("视觉证据来源不属于当前生成上下文：" + "、".join(unknown_ids[:5]))
        suggestions.append("仅保留当前项目上下文中真实存在的 evidence_id")

    used = [evidence_by_id[eid] for eid in evidence_sources if eid in evidence_by_id]
    if not used and evidence_sources:
        return {
            "score": max(0.0, score),
            "status": "invalid_sources",
            "issues": issues,
            "suggestions": suggestions,
            "reasons": reasons
            or ["存在 evidence_sources，但未能匹配到当前上下文视觉证据。"],
        }

    assumptions_text = json.dumps(case.get("assumptions") or [], ensure_ascii=False)
    missing_text = json.dumps(case.get("missing_information") or [], ensure_ascii=False)
    has_text_sources = bool(case.get("source_chunk_ids"))
    for evidence in used:
        if evidence.get("need_human_confirm") and not has_text_sources:
            score -= 35
            issues.append(
                f"视觉证据 {evidence.get('evidence_id')} 需人工确认，且用例缺少文本来源支撑"
            )
            suggestions.append("需人工确认的视觉证据不可作为唯一测试依据")
        if (
            evidence.get("need_human_confirm")
            and "视觉" not in assumptions_text
            and "人工确认" not in assumptions_text
        ):
            score -= 15
            issues.append(
                f"视觉证据 {evidence.get('evidence_id')} 需人工确认，但 assumptions 未说明"
            )

    conflict_markers = ("冲突", "不一致", "矛盾")
    visual_risks = json.dumps([e.get("risk_points") for e in used], ensure_ascii=False)
    if any(marker in visual_risks for marker in conflict_markers) and not any(
        marker in assumptions_text + missing_text for marker in conflict_markers
    ):
        score -= 25
        issues.append(
            "视觉证据风险点提示可能冲突，但用例未在 assumptions 或 missing_information 标记"
        )
        suggestions.append("如视觉证据与文本需求冲突，应以文本需求为准并显式标记冲突")

    if used and has_text_sources and not issues:
        if any(e.get("possible_test_points") for e in used):
            score = min(100.0, score + 10)
            reasons.append("视觉证据作为辅助补充了 UI 控件、流程节点或界面状态测试点。")
        else:
            reasons.append("视觉证据已标记来源，且没有覆盖文本需求。")

    status = "ok" if not issues else "needs_review"
    return {
        "score": round(max(0.0, min(100.0, score)), 1),
        "status": status,
        "issues": issues,
        "suggestions": suggestions,
        "reasons": reasons,
    }


def evaluate_case_quality(
    case: Dict[str, Any], context: Dict[str, Any]
) -> Dict[str, Any]:
    """Score six quality dimensions and flag unsupported numeric metrics."""
    requirement = str((context.get("requirement") or {}).get("description") or "")
    scenario_text = json.dumps(
        context.get("related_scenario_cards") or [], ensure_ascii=False
    )
    source_text = (
        requirement
        + json.dumps(context.get("related_chunks") or [], ensure_ascii=False)
        + scenario_text
    )
    # Persistence identifiers contain random digits and are not test metrics.
    # Excluding provenance/metadata prevents run IDs and chunk IDs from being
    # misclassified as unsupported numeric facts.
    business_fields = {
        "title", "case_name", "objective", "test_purpose", "preconditions",
        "prerequisites", "test_data", "input_data", "test_steps",
        "expected_results", "expected_result", "evaluation_criteria",
        "pass_criteria",
    }
    case_text = json.dumps(
        {
            k: v
            for k, v in case.items()
            if k in business_fields
        },
        ensure_ascii=False,
    )
    issues = []
    suggestions = []
    dimensions = {}

    req_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", requirement))
    case_tokens = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", case_text))
    overlap = len(req_tokens & case_tokens) / max(len(req_tokens), 1)
    dimensions["需求覆盖度"] = min(100, round(45 + overlap * 80)) if requirement else 30

    scenario_markers = [
        str(s.get("scenario_name") or "")
        for s in context.get("related_scenario_cards") or []
    ]
    scenario_hits = sum(
        1
        for x in scenario_markers
        if x and (x in case_text or any(t in case_text for t in x[:12].split()))
    )
    has_environment = bool(case.get("test_environment"))
    has_interface = bool(case.get("related_interfaces"))
    dimensions["场景贴合度"] = min(
        100, 35 + scenario_hits * 25 + 20 * has_environment + 20 * has_interface
    )

    steps = case.get("test_steps") or []
    if isinstance(steps, str):
        steps = [x for x in steps.splitlines() if x.strip()]
    executable = (
        len(steps) >= 3
        and bool(case.get("input_data"))
        and any(
            k in json.dumps(steps, ensure_ascii=False)
            for k in ("输入", "调用", "发送", "操作", "记录", "检查")
        )
    )
    dimensions["步骤可执行性"] = 90 if executable else (60 if steps else 20)
    if not executable:
        issues.append("测试步骤尚未完整体现可执行操作与输入数据")
        suggestions.append("补充操作者、输入、接口调用、状态观察和记录动作")

    criteria = str(case.get("pass_criteria") or "")
    dimensions["判定准则明确性"] = (
        90 if criteria and criteria not in ("需人工确认",) else (60 if criteria else 20)
    )
    if not criteria:
        issues.append("缺少判定准则")

    source_ids = set(case.get("source_chunk_ids") or [])
    valid_ids = {str(x.get("chunk_id")) for x in context.get("related_chunks") or []}
    trace_ok = bool(source_ids) and source_ids.issubset(valid_ids)
    dimensions["来源可追溯性"] = 100 if trace_ok else (50 if source_ids else 15)
    if not trace_ok:
        issues.append("来源片段缺失或不属于本次检索上下文")
        suggestions.append("仅保留上下文中真实存在的 source_chunk_ids")

    source_numbers = set(re.findall(r"\d+(?:\.\d+)?", source_text))
    case_numbers = set(re.findall(r"\d+(?:\.\d+)?", case_text))
    unsupported = sorted(case_numbers - source_numbers)
    dimensions["无臆造指标"] = 100 if not unsupported else 20
    if unsupported:
        issues.append("疑似存在资料未支持的数值指标：" + "、".join(unsupported[:8]))
        suggestions.append("删除无来源数值，改为“按需求文档规定值判定”或“需人工确认”")

    if case.get("need_human_confirm"):
        suggestions.append("生成结果已标记需人工确认，请补齐 missing_information")
    visual_usage = _visual_evidence_usage(case, context, case_text)
    if visual_usage["score"] is not None:
        dimensions["视觉证据使用合理性"] = visual_usage["score"]
        issues.extend(visual_usage["issues"])
        suggestions.extend(visual_usage["suggestions"])
        base_score = sum(
            value for key, value in dimensions.items() if key != "视觉证据使用合理性"
        ) / max(len(dimensions) - 1, 1)
        score = round(base_score * 0.9 + visual_usage["score"] * 0.1, 1)
    else:
        score = round(sum(dimensions.values()) / len(dimensions), 1)
    return {
        "score": score,
        "dimensions": dimensions,
        "issues": issues,
        "suggestions": list(dict.fromkeys(suggestions)),
        "has_fabricated_metrics": bool(unsupported),
        "visual_evidence_usage_score": visual_usage,
    }
