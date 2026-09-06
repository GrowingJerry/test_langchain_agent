"""Fault-isolated, streaming JSONL import for equipment reference data."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional

from infrastructure.equipment.import_report import EquipmentImportError, EquipmentImportReport
from infrastructure.equipment.normalizer import EquipmentFieldMapping, normalize_equipment_record
from infrastructure.repositories.equipment_repository import EquipmentRepository


class MilitaryJsonlImporter:
    def __init__(
        self,
        repository: EquipmentRepository,
        mapping: Optional[EquipmentFieldMapping] = None,
    ) -> None:
        self.repository = repository
        self.mapping = mapping or EquipmentFieldMapping()

    def import_file(self, path: Path, project_id: str) -> EquipmentImportReport:
        """Import one file line by line; a malformed record never stops the stream."""
        source_path = Path(path)
        report = EquipmentImportReport(
            source_file=str(source_path), project_id=project_id
        )
        coverage: Counter[str] = Counter()
        with source_path.open("r", encoding="utf-8-sig") as source:
            for line_no, raw_line in enumerate(source, start=1):
                report.total_lines += 1
                stripped = raw_line.strip()
                if not stripped:
                    report.skipped_count += 1
                    continue
                try:
                    payload: Any = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    report.failure_count += 1
                    self._record_error(
                        report,
                        line_no,
                        "invalid_json",
                        str(exc),
                        stripped,
                    )
                    continue
                if not isinstance(payload, dict):
                    report.failure_count += 1
                    self._record_error(
                        report,
                        line_no,
                        "invalid_record_type",
                        "JSONL record must be an object",
                        stripped,
                    )
                    continue
                coverage.update(str(key) for key in payload)
                try:
                    normalized: Dict[str, Any] = normalize_equipment_record(
                        payload,
                        project_id,
                        str(source_path),
                        line_no,
                        self.mapping,
                    )
                    if not normalized["name"]:
                        report.missing_name_count += 1
                        report.skipped_count += 1
                        self._record_error(
                            report,
                            line_no,
                            "missing_name",
                            "No configured name field contained a value",
                            stripped,
                        )
                        continue
                    status = self.repository.upsert_equipment(project_id, normalized)
                    if status == "duplicate":
                        report.duplicate_count += 1
                    else:
                        report.success_count += 1
                except Exception as exc:
                    report.failure_count += 1
                    self._record_error(
                        report,
                        line_no,
                        type(exc).__name__,
                        str(exc),
                        stripped,
                    )
        report.field_coverage = dict(sorted(coverage.items()))
        return report

    def _record_error(
        self,
        report: EquipmentImportReport,
        line_no: int,
        error_type: str,
        error_message: str,
        raw_line: str,
    ) -> None:
        error = EquipmentImportError(
            source_line_no=line_no,
            error_type=error_type,
            error_message=error_message,
            raw_line=raw_line[:1000],
        )
        report.add_error(error)
        self.repository.record_import_error(
            report.project_id,
            report.source_file,
            line_no,
            error_type,
            error_message,
            raw_line,
        )

