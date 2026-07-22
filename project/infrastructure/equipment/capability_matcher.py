"""Deterministic role, capability, interface, constraint, and inventory matching."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from domain.schemas.equipment import EquipmentMatch


def _normalized(values: Iterable[Any]) -> Dict[str, str]:
    return {
        text.casefold(): text
        for value in values
        if (text := str(value or "").strip())
    }


def equipment_source_refs(candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build source references without discarding JSONL or document positions."""
    refs: List[Dict[str, Any]] = []
    if candidate.get("source_file") or candidate.get("source_line_no"):
        refs.append(
            {
                "source_type": "jsonl",
                "project_id": str(candidate.get("project_id") or ""),
                "source_file": str(candidate.get("source_file") or ""),
                "source_line_no": candidate.get("source_line_no"),
                "jsonl_record_no": candidate.get("jsonl_record_no"),
                "record_hash": str(candidate.get("record_hash") or ""),
            }
        )
    if candidate.get("document_id") or candidate.get("chunk_id"):
        refs.append(
            {
                "source_type": "document",
                "project_id": str(candidate.get("project_id") or ""),
                "document_id": str(candidate.get("document_id") or ""),
                "chunk_id": str(candidate.get("chunk_id") or ""),
                "page_no": candidate.get("page_no"),
            }
        )
    for fact_type, facts in (
        ("capability", candidate.get("capabilities") or []),
        ("role_mapping", candidate.get("role_mappings") or []),
    ):
        for fact in facts:
            if not any(
                fact.get(key)
                for key in ("document_id", "chunk_id", "page_no", "jsonl_record_no")
            ):
                continue
            refs.append(
                {
                    "source_type": fact_type,
                    "project_id": str(candidate.get("project_id") or ""),
                    "document_id": str(fact.get("document_id") or ""),
                    "chunk_id": str(fact.get("chunk_id") or ""),
                    "page_no": fact.get("page_no"),
                    "jsonl_record_no": fact.get("jsonl_record_no"),
                }
            )
    unique: List[Dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for ref in refs:
        key = tuple(sorted(ref.items()))
        if key not in seen:
            seen.add(key)
            unique.append(ref)
    return unique


class CapabilityMatcher:
    """Make the final applicability decision without an LLM."""

    def match(
        self,
        candidate: Dict[str, Any],
        *,
        name_query: str = "",
        category_query: str = "",
        required_roles: Optional[List[str]] = None,
        required_capabilities: Optional[List[str]] = None,
        required_interfaces: Optional[List[str]] = None,
        prohibited_constraints: Optional[List[str]] = None,
        inventory_quantity: Optional[int] = None,
        required_quantity: int = 1,
        semantic_score: float = 0.0,
    ) -> EquipmentMatch:
        roles = _normalized(candidate.get("roles") or [])
        capabilities = _normalized(
            capability.get("name")
            for capability in candidate.get("capabilities") or []
        )
        interfaces = _normalized(candidate.get("interfaces") or [])
        constraints = _normalized(candidate.get("constraints") or [])
        aliases = _normalized(candidate.get("aliases") or [])
        requested_roles = _normalized(required_roles or [])
        requested_capabilities = _normalized(required_capabilities or [])
        requested_interfaces = _normalized(required_interfaces or [])
        prohibited = _normalized(prohibited_constraints or [])

        matched_roles = [
            original for key, original in requested_roles.items() if key in roles
        ]
        missing_roles = [
            original for key, original in requested_roles.items() if key not in roles
        ]
        matched_capabilities = [
            original
            for key, original in requested_capabilities.items()
            if key in capabilities
        ]
        missing_capabilities = [
            original
            for key, original in requested_capabilities.items()
            if key not in capabilities
        ]
        violated = [
            f"约束冲突:{prohibited[key]}"
            for key in prohibited.keys() & constraints.keys()
        ]
        violated.extend(f"缺少角色:{role}" for role in missing_roles)
        violated.extend(
            f"缺少兼容接口:{original}"
            for key, original in requested_interfaces.items()
            if key not in interfaces
        )
        if inventory_quantity is not None and inventory_quantity < required_quantity:
            violated.append(
                f"当前项目库存不足:需要{required_quantity},可用{inventory_quantity}"
            )

        reasons: List[str] = []
        score = 0.0
        query = str(name_query or "").strip().casefold()
        candidate_name = str(candidate.get("name") or "").strip()
        if query and query == candidate_name.casefold():
            score += 100.0
            reasons.append("精确名称匹配")
        elif query and query in aliases:
            score += 95.0
            reasons.append("精确别名匹配")
        elif query and query in candidate_name.casefold():
            score += 65.0
            reasons.append("名称部分匹配")
        if category_query and str(candidate.get("category") or "").casefold() == str(
            category_query
        ).casefold():
            score += 40.0
            reasons.append("类别精确匹配")

        if requested_roles:
            coverage = len(matched_roles) / len(requested_roles)
            score += 25.0 * coverage
            reasons.append(f"角色覆盖{len(matched_roles)}/{len(requested_roles)}")
        if requested_capabilities:
            coverage = len(matched_capabilities) / len(requested_capabilities)
            score += 45.0 * coverage
            reasons.append(
                f"能力覆盖{len(matched_capabilities)}/{len(requested_capabilities)}"
            )
        if requested_interfaces:
            matched_interfaces = sum(
                key in interfaces for key in requested_interfaces
            )
            score += 15.0 * matched_interfaces / len(requested_interfaces)
            reasons.append(
                f"接口兼容{matched_interfaces}/{len(requested_interfaces)}"
            )
        if semantic_score > 0:
            score += min(semantic_score, 100.0) * 0.1
            reasons.append(f"描述相似度补充{semantic_score:.1f}")
        if violated:
            score = 0.0
            reasons.append("结构化约束校验未通过")
        elif missing_capabilities:
            reasons.append("存在缺失能力，不能判定为完全适用")
        else:
            reasons.append("结构化校验通过")

        source_refs = equipment_source_refs(candidate)
        need_confirm = bool(
            candidate.get("need_human_confirm")
            or missing_capabilities
            or violated
            or not source_refs
        )
        return EquipmentMatch(
            equipment_id=str(candidate.get("equipment_id") or ""),
            name=candidate_name,
            matched_roles=matched_roles,
            matched_capabilities=matched_capabilities,
            missing_capabilities=missing_capabilities,
            violated_constraints=violated,
            score=round(min(max(score, 0.0), 100.0), 2),
            match_reason="；".join(reasons),
            source_refs=source_refs,
            need_human_confirm=need_confirm,
        )
