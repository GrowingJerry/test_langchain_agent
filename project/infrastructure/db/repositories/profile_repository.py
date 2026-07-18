"""Project profile persistence."""

import json
from typing import Any, Dict, Optional

from infrastructure.db.repositories.base import BaseRepository, now_iso


class ProfileRepository(BaseRepository):
    _LIST_FIELDS = ("main_functions", "interfaces", "quality_attributes", "constraints")

    def save(self, project_id: str, profile: Dict[str, Any]) -> None:
        with self.connections.transaction() as conn:
            conn.execute(
                """INSERT INTO project_profiles VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id) DO UPDATE SET project_name=excluded.project_name,domain=excluded.domain,
                test_object=excluded.test_object,main_functions=excluded.main_functions,interfaces=excluded.interfaces,
                quality_attributes=excluded.quality_attributes,constraints=excluded.constraints,raw_json=excluded.raw_json,updated_at=excluded.updated_at""",
                (
                    project_id,
                    profile.get("project_name", ""),
                    profile.get("domain", ""),
                    profile.get("test_object", ""),
                    *(
                        json.dumps(profile.get(key, []), ensure_ascii=False)
                        for key in self._LIST_FIELDS
                    ),
                    json.dumps(profile, ensure_ascii=False),
                    now_iso(),
                ),
            )

    def get(self, project_id: str) -> Optional[Dict[str, Any]]:
        with self.connections.connection() as conn:
            row = conn.execute(
                "SELECT * FROM project_profiles WHERE project_id=?", (project_id,)
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        for key in self._LIST_FIELDS:
            try:
                item[key] = json.loads(item.get(key) or "[]")
            except json.JSONDecodeError:
                item[key] = []
        return item
