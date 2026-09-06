"""Project persistence."""

from typing import Any, Dict, List, Optional

from infrastructure.repositories.base import BaseRepository, new_id, now_iso


class ProjectRepository(BaseRepository):
    def create(self, project_name: str, description: str = "") -> Dict[str, Any]:
        name = (project_name or "").strip()
        if not name:
            raise ValueError("project_name is required")
        project_id, timestamp = new_id("PRJ"), now_iso()
        with self.connections.transaction() as conn:
            conn.execute(
                "INSERT INTO projects VALUES (?, ?, ?, ?, ?)",
                (project_id, name, description or "", timestamp, timestamp),
            )
        return self.get(project_id) or {}

    def list(self) -> List[Dict[str, Any]]:
        with self.connections.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM projects ORDER BY updated_at DESC, created_at DESC"
                )
            ]

    def get(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self.connections.connection() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            return dict(row) if row else None

    def touch(self, conn: Any, project_id: str) -> None:
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE project_id = ?",
            (now_iso(), project_id),
        )
