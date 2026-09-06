"""Project-scoped orchestration for deterministic equipment quantity allocation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from domain.rules.equipment_allocation import AllocationRule
from infrastructure.equipment.capability_matcher import equipment_source_refs
from infrastructure.repositories.equipment_repository import EquipmentRepository
from workflows.scenario.quantity_solver import QuantitySolution, QuantitySolver


class EquipmentAllocator:
    """Bind rules to explicit equipment scope and current-project inventory."""

    def __init__(
        self,
        repository: EquipmentRepository,
        solver: Optional[QuantitySolver] = None,
    ) -> None:
        self.repository = repository
        self.solver = solver or QuantitySolver()

    def allocate(
        self,
        project_id: str,
        rules: List[AllocationRule],
        *,
        input_values: Optional[Dict[str, Any]] = None,
        manual_quantities: Optional[Dict[str, int]] = None,
        requested_allocations: Optional[List[Tuple[str, str]]] = None,
        allow_global: bool = False,
    ) -> List[QuantitySolution]:
        equipment_ids = {
            rule.equipment_id for rule in rules if rule.equipment_id
        } | {equipment_id for _, equipment_id in requested_allocations or []}
        equipment_by_id = {}
        for equipment_id in sorted(equipment_ids):
            equipment = self.repository.get_equipment(
                project_id, equipment_id, allow_global=allow_global
            )
            if not equipment:
                raise ValueError(
                    f"equipment {equipment_id!r} is outside the explicit project scope"
                )
            equipment_by_id[equipment_id] = equipment
        inventory = {
            equipment_id: self.repository.inventory_quantity(project_id, equipment_id)
            for equipment_id in equipment_ids
        }
        solutions = self.solver.solve(
            rules,
            input_values=input_values,
            inventory=inventory,
            manual_quantities=manual_quantities,
            requested_allocations=requested_allocations,
        )
        for solution in solutions:
            refs = equipment_source_refs(equipment_by_id[solution.equipment_id])
            solution.source_refs = self._merge_refs(solution.source_refs, refs)
        return solutions

    @staticmethod
    def _merge_refs(
        left: List[Dict[str, Any]], right: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen: set[Tuple[Tuple[str, str], ...]] = set()
        for ref in [*left, *right]:
            key = tuple(sorted((str(name), str(value)) for name, value in ref.items()))
            if key not in seen:
                seen.add(key)
                merged.append(ref)
        return merged
