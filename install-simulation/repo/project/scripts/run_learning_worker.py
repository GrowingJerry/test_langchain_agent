"""Run the dependency-free SQLite long-document worker."""

from __future__ import annotations

import argparse
import socket
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application.services.project_service import ProjectManager  # noqa: E402
from workflows.learning.document_job_runner import DocumentJobRunner  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--worker-id", default=f"{socket.gethostname()}-{__import__('os').getpid()}")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    manager = ProjectManager(args.db) if args.db else ProjectManager()
    runner = DocumentJobRunner(manager)
    while True:
        result = runner.run_once(args.worker_id)
        if args.once:
            return 0
        if result is None:
            time.sleep(max(args.poll_seconds, 0.1))


if __name__ == "__main__":
    raise SystemExit(main())
