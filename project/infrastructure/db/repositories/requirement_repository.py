"""Project requirement persistence."""

from typing import Any, Dict, List, Optional

from infrastructure.db.repositories.base import BaseRepository, now_iso


class RequirementRepository(BaseRepository):
    def replace(self, project_id: str, rows: List[Dict[str, Any]]) -> None:
        with self.connections.transaction() as conn:
            conn.execute(
                "DELETE FROM project_requirements WHERE project_id=?", (project_id,)
            )
            for row in rows:
                self._upsert(conn, project_id, row)

    def upsert(self, project_id: str, row: Dict[str, Any]) -> None:
        with self.connections.transaction() as conn:
            self._upsert(conn, project_id, row)

    @staticmethod
    def _upsert(conn: Any, project_id: str, row: Dict[str, Any]) -> None:
        conn.execute(
            """INSERT INTO project_requirements VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id,requirement_id) DO UPDATE SET title=excluded.title,description=excluded.description,
            category=excluded.category,source_document=excluded.source_document,source_chunk_id=excluded.source_chunk_id""",
            (
                row.get("requirement_id", ""),
                project_id,
                row.get("title", ""),
                row.get("description", ""),
                row.get("category", ""),
                row.get("source_document", "manual_input"),
                row.get("source_chunk_id", ""),
                now_iso(),
            ),
        )

    def list(self, project_id: str) -> List[Dict[str, Any]]:
        with self.connections.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM project_requirements WHERE project_id=? ORDER BY requirement_id",
                    (project_id,),
                )
            ]

    def get(self, project_id: str, requirement_id: str) -> Optional[Dict[str, Any]]:
        with self.connections.connection() as conn:
            row = conn.execute(
                "SELECT * FROM project_requirements WHERE project_id=? AND requirement_id=?",
                (project_id, requirement_id),
            ).fetchone()
            return dict(row) if row else None
