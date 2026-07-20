"""Configurable normalization for the observed military JSONL field vocabulary."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from infrastructure.db.json_codec import dumps_json


class EquipmentFieldMapping(BaseModel):
    """Map source fields without requiring every JSONL producer to share a schema."""

    model_config = ConfigDict(extra="forbid")

    name_fields: List[str] = Field(default_factory=lambda: ["name"])
    alias_fields: List[str] = Field(default_factory=list)
    category_fields: List[str] = Field(default_factory=lambda: ["大类"])
    platform_type_fields: List[str] = Field(default_factory=lambda: ["类型"])
    role_fields: List[str] = Field(default_factory=list)
    capability_fields: List[str] = Field(
        default_factory=lambda: [
            "飞行速度",
            "最大飞行速度",
            "最大航程",
            "航速",
            "续航距离",
            "潜航深度",
            "自持力",
            "最大速度",
            "最大行程",
            "最大射程",
            "有效射程",
            "射程",
            "炮口初速",
            "战斗射速",
        ]
    )
    interface_fields: List[str] = Field(default_factory=list)
    constraint_fields: List[str] = Field(default_factory=list)
    minimum_unit_fields: List[str] = Field(default_factory=list)
    maximum_unit_fields: List[str] = Field(default_factory=list)
    description_fields: List[str] = Field(default_factory=lambda: ["content"])
    ignored_parameter_fields: List[str] = Field(default_factory=lambda: ["image"])


def _first_value(payload: Dict[str, Any], fields: Iterable[str]) -> Any:
    for field in fields:
        value = payload.get(field)
        if value is not None and str(value).strip():
            return value
    return ""


def _as_string_list(payload: Dict[str, Any], fields: Iterable[str]) -> List[str]:
    result: List[str] = []
    for field in fields:
        value = payload.get(field)
        values = value if isinstance(value, list) else [value]
        for item in values:
            for part in re.split(r"[,，、;；\n]+", str(item or "")):
                normalized = part.strip()
                if normalized and normalized not in result:
                    result.append(normalized)
    return result


def _unit_value(payload: Dict[str, Any], fields: Iterable[str]) -> Optional[int]:
    value = _first_value(payload, fields)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None


def _derived_aliases(name: str) -> List[str]:
    if not name:
        return []
    parts = [part.strip(" \t\"'“”") for part in re.split(r"[/／]", name)]
    return [part for part in dict.fromkeys(parts) if part and part != name]


def stable_record_hash(payload: Dict[str, Any]) -> str:
    canonical = dumps_json(payload, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stable_equipment_id(
    project_id: str, name: str, category: str, platform_type: str
) -> str:
    identity = "\x1f".join((project_id, name, category, platform_type))
    return f"EQ-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:20]}"


def normalize_equipment_record(
    payload: Dict[str, Any],
    project_id: str,
    source_file: str,
    source_line_no: int,
    mapping: Optional[EquipmentFieldMapping] = None,
) -> Dict[str, Any]:
    """Normalize one object while retaining the complete source payload."""
    fields = mapping or EquipmentFieldMapping()
    name = str(_first_value(payload, fields.name_fields)).strip()
    category = str(_first_value(payload, fields.category_fields)).strip()
    platform_type = str(
        _first_value(payload, fields.platform_type_fields)
    ).strip()
    aliases = _as_string_list(payload, fields.alias_fields)
    for alias in _derived_aliases(name):
        if alias not in aliases:
            aliases.append(alias)
    capabilities = [
        {"name": field, "description": str(payload[field]).strip()}
        for field in fields.capability_fields
        if payload.get(field) is not None and str(payload[field]).strip()
    ]
    mapped_fields = {
        *fields.name_fields,
        *fields.alias_fields,
        *fields.category_fields,
        *fields.platform_type_fields,
        *fields.role_fields,
        *fields.capability_fields,
        *fields.interface_fields,
        *fields.constraint_fields,
        *fields.minimum_unit_fields,
        *fields.maximum_unit_fields,
        *fields.description_fields,
        *fields.ignored_parameter_fields,
    }
    simulation_parameters = {
        key: value for key, value in payload.items() if key not in mapped_fields
    }
    return {
        "equipment_id": stable_equipment_id(
            project_id, name, category, platform_type
        ),
        "project_id": project_id,
        "name": name,
        "aliases": aliases,
        "category": category,
        "platform_type": platform_type,
        "roles": _as_string_list(payload, fields.role_fields),
        "capabilities": capabilities,
        "interfaces": _as_string_list(payload, fields.interface_fields),
        "constraints": _as_string_list(payload, fields.constraint_fields),
        "simulation_parameters": simulation_parameters,
        "minimum_unit": _unit_value(payload, fields.minimum_unit_fields),
        "maximum_unit": _unit_value(payload, fields.maximum_unit_fields),
        "source_description": str(
            _first_value(payload, fields.description_fields)
        ).strip(),
        "source_file": source_file,
        "source_line_no": source_line_no,
        "raw_payload_json": dumps_json(payload),
        "record_hash": stable_record_hash(payload),
    }
