"""Run the persistent test-case generation worker."""
from __future__ import annotations

import argparse
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application.services.project_service import ProjectManager
from workflows.generation_job_runner import GenerationJobRunner


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--idle-exit-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    runner = GenerationJobRunner(ProjectManager(args.db))
    idle_since = time.monotonic()
    while True:
        result = runner.run_once()
        if args.once: return 0
        if result is None:
            if time.monotonic() - idle_since >= args.idle_exit_seconds: return 0
            time.sleep(max(args.poll_seconds, .1))
        else:
            idle_since = time.monotonic()


if __name__ == "__main__":
    raise SystemExit(main())
