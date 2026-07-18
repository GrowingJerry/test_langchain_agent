"""Artifact trace-source persistence."""

from typing import Any, Dict, List

from infrastructure.db.repositories.base import BaseRepository, new_id, now_iso


class TraceRepository(BaseRepository):
    def replace_for_case(
        self, conn: Any, project_id: str, case_id: str, chunks: List[Dict[str, Any]]
    ) -> None:
        conn.execute(
            "DELETE FROM trace_sources WHERE project_id=? AND artifact_id=?",
            (project_id, case_id),
        )
        for chunk in chunks:
            conn.execute(
                "INSERT INTO trace_sources VALUES(?,?,?,?,?,?,?)",
                (
                    new_id("TRC"),
                    project_id,
                    "generated_case",
                    case_id,
                    str(chunk.get("filename") or ""),
                    str(chunk.get("chunk_id") or ""),
                    now_iso(),
                ),
            )

    def list(self, project_id: str) -> List[Dict[str, Any]]:
        with self.connections.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM trace_sources WHERE project_id=? ORDER BY artifact_id,created_at",
                    (project_id,),
                )
            ]
