"""Generated-case review persistence."""

import json
from typing import Any, Dict, List

from infrastructure.db.repositories.base import BaseRepository, new_id, now_iso


class ReviewRepository(BaseRepository):
    def save(
        self,
        project_id: str,
        case_id: str,
        review_type: str,
        status: str,
        issues: List[str],
    ) -> None:
        with self.connections.transaction() as conn:
            conn.execute(
                "DELETE FROM review_results WHERE project_id=? AND case_id=? AND review_type=?",
                (project_id, case_id, review_type),
            )
            conn.execute(
                "INSERT INTO review_results VALUES(?,?,?,?,?,?,?)",
                (
                    new_id("REV"),
                    project_id,
                    case_id,
                    review_type,
                    status,
                    json.dumps(issues, ensure_ascii=False),
                    now_iso(),
                ),
            )

    def list(self, project_id: str) -> List[Dict[str, Any]]:
        with self.connections.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM review_results WHERE project_id=? ORDER BY reviewed_at DESC",
                (project_id,),
            ).fetchall()
        result = [dict(row) for row in rows]
        for row in result:
            try:
                row["issues"] = json.loads(row.get("issues_json") or "[]")
            except json.JSONDecodeError:
                row["issues"] = []
        return result
