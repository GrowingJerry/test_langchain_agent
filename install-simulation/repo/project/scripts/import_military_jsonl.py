"""Import military equipment JSONL into an explicitly scoped equipment library."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from application.services.project_service import ProjectManager  # noqa: E402
from infrastructure.equipment.jsonl_importer import MilitaryJsonlImporter  # noqa: E402
from infrastructure.database.json_codec import dumps_json  # noqa: E402
from infrastructure.repositories.equipment_repository import (  # noqa: E402
    GLOBAL_PROJECT_ID,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream a JSONL file into the project-scoped equipment repository."
    )
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manager = ProjectManager()
    project_id = str(args.project_id).strip()
    if project_id == GLOBAL_PROJECT_ID:
        manager.equipment.ensure_global_project()
    elif not manager.get_project(project_id):
        print(f"Project {project_id!r} does not exist", file=sys.stderr)
        return 2
    report = MilitaryJsonlImporter(manager.equipment).import_file(
        args.file, project_id
    )
    print(dumps_json(report.model_dump(mode="json")))
    return 0 if report.failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

