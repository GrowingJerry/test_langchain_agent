"""Repository implementations reserved for staged migration."""

from infrastructure.db.repositories.equipment_repository import (
    GLOBAL_PROJECT_ID,
    EquipmentRepository,
)

__all__ = ["EquipmentRepository", "GLOBAL_PROJECT_ID"]
