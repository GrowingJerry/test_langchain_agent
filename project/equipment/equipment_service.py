"""Application service for explainable equipment retrieval and role matching."""

from __future__ import annotations

from typing import List, Optional

from domain.schemas.equipment import EquipmentMatch, EquipmentSearchResult
from equipment.capability_matcher import CapabilityMatcher
from infrastructure.db.repositories.equipment_repository import EquipmentRepository
from infrastructure.retrieval.equipment_retriever import EquipmentRetriever


class EquipmentService:
    def __init__(
        self,
        repository: EquipmentRepository,
        retriever: Optional[EquipmentRetriever] = None,
        matcher: Optional[CapabilityMatcher] = None,
    ) -> None:
        self.repository = repository
        self.retriever = retriever or EquipmentRetriever(repository)
        self.matcher = matcher or CapabilityMatcher()

    def search(
        self,
        project_id: str,
        *,
        name: str = "",
        category: str = "",
        roles: Optional[List[str]] = None,
        capabilities: Optional[List[str]] = None,
        interfaces: Optional[List[str]] = None,
        prohibited_constraints: Optional[List[str]] = None,
        inventory_only: bool = False,
        required_quantity: int = 1,
        description: str = "",
        allow_global: bool = False,
        limit: int = 100,
    ) -> EquipmentSearchResult:
        """Retrieve candidates, then deterministically accept or reject each one."""
        if required_quantity < 1:
            raise ValueError("required_quantity must be at least 1")
        retrieved = self.retriever.retrieve(
            project_id,
            name=name,
            category=category,
            description=description,
            allow_global=allow_global,
            limit=limit,
        )
        matches: List[EquipmentMatch] = []
        rejected: List[EquipmentMatch] = []
        for candidate in retrieved.candidates:
            inventory_quantity = (
                self.repository.inventory_quantity(
                    project_id, str(candidate.get("equipment_id") or "")
                )
                if inventory_only
                else None
            )
            result = self.matcher.match(
                candidate,
                name_query=name,
                category_query=category,
                required_roles=roles,
                required_capabilities=capabilities,
                required_interfaces=interfaces,
                prohibited_constraints=prohibited_constraints,
                inventory_quantity=inventory_quantity,
                required_quantity=required_quantity,
                semantic_score=float(candidate.get("_semantic_score") or 0),
            )
            if result.violated_constraints or result.missing_capabilities:
                rejected.append(result)
            else:
                matches.append(result)
        matches.sort(
            key=lambda item: (
                self._name_match_rank(item),
                -item.score,
                item.name,
                item.equipment_id,
            )
        )
        rejected.sort(key=lambda item: (-item.score, item.name, item.equipment_id))
        missing = self._missing_information(
            name,
            category,
            roles or [],
            capabilities or [],
            interfaces or [],
            inventory_only,
            retrieved.candidates,
            matches,
            rejected,
        )
        return EquipmentSearchResult(
            project_id=project_id,
            matches=matches,
            rejected_matches=rejected,
            missing_information=missing,
            used_embedding=retrieved.used_embedding,
        )

    @staticmethod
    def _name_match_rank(item: EquipmentMatch) -> int:
        if item.match_reason.startswith("精确名称匹配"):
            return 0
        if item.match_reason.startswith("精确别名匹配"):
            return 1
        if item.match_reason.startswith("名称部分匹配"):
            return 2
        return 3

    def find_exact(
        self, project_id: str, name_or_alias: str, allow_global: bool = False
    ) -> EquipmentSearchResult:
        result = self.search(
            project_id, name=name_or_alias, allow_global=allow_global
        )
        query = name_or_alias.strip().casefold()
        result.matches = [
            item
            for item in result.matches
            if item.match_reason.startswith(("精确名称匹配", "精确别名匹配"))
        ]
        if not result.matches:
            result.missing_information = [f"未找到精确名称或别名：{query}"]
        return result

    def find_by_category(
        self, project_id: str, category: str, allow_global: bool = False
    ) -> EquipmentSearchResult:
        return self.search(
            project_id, category=category, allow_global=allow_global
        )

    def find_by_role(
        self, project_id: str, role: str, allow_global: bool = False
    ) -> EquipmentSearchResult:
        return self.search(project_id, roles=[role], allow_global=allow_global)

    def find_by_capabilities(
        self,
        project_id: str,
        capabilities: List[str],
        allow_global: bool = False,
    ) -> EquipmentSearchResult:
        return self.search(
            project_id,
            capabilities=capabilities,
            allow_global=allow_global,
        )

    @staticmethod
    def _missing_information(
        name: str,
        category: str,
        roles: List[str],
        capabilities: List[str],
        interfaces: List[str],
        inventory_only: bool,
        candidates: List[dict],
        matches: List[EquipmentMatch],
        rejected: List[EquipmentMatch],
    ) -> List[str]:
        if matches:
            return []
        if not candidates:
            criteria = name or category or "、".join(
                [*roles, *capabilities, *interfaces]
            )
            return [f"装备库中没有候选装备：{criteria or '未提供检索条件'}"]
        missing: List[str] = []
        if inventory_only and any(
            any("库存不足" in value for value in item.violated_constraints)
            for item in rejected
        ):
            missing.append("当前项目库存中没有满足数量要求的装备")
        missing_capabilities = list(
            dict.fromkeys(
                capability
                for item in rejected
                for capability in item.missing_capabilities
            )
        )
        if missing_capabilities:
            missing.append("缺少能力：" + "、".join(missing_capabilities))
        if not missing:
            missing.append("候选装备均未通过角色、接口或约束校验")
        return missing
