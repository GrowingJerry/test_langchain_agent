"""Structured result of one streaming equipment JSONL import."""

from __future__ import annotations

from typing import Dict, List

from pydantic import BaseModel, ConfigDict, Field


class EquipmentImportError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_line_no: int
    error_type: str
    error_message: str
    raw_line: str = ""


class EquipmentImportReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file: str
    project_id: str
    total_lines: int = 0
    success_count: int = 0
    skipped_count: int = 0
    duplicate_count: int = 0
    failure_count: int = 0
    missing_name_count: int = 0
    field_coverage: Dict[str, int] = Field(default_factory=dict)
    errors: List[EquipmentImportError] = Field(default_factory=list)

    def add_error(self, error: EquipmentImportError) -> None:
        if len(self.errors) < 20:
            self.errors.append(error)

