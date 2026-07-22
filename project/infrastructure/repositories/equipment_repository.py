"""Project-scoped equipment persistence and explicit GLOBAL-library access."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Sequence

from infrastructure.database.json_codec import dumps_json, loads_json
from infrastructure.repositories.base import BaseRepository, new_id, now_iso


GLOBAL_PROJECT_ID = "GLOBAL"


class EquipmentRepository(BaseRepository):
    """Persist normalized equipment without implicit cross-project reads."""

    @staticmethod
    def _scopes(project_id: str, allow_global: bool) -> List[str]:
        scope = str(project_id or "").strip()
        if not scope:
            raise ValueError("project_id is required")
        return (
            [scope, GLOBAL_PROJECT_ID]
            if allow_global and scope != GLOBAL_PROJECT_ID
            else [scope]
        )

    def ensure_global_project(self) -> None:
        """Create the explicit organization library boundary if it is absent."""
        timestamp = now_iso()
        with self.connections.transaction() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO projects(project_id,project_name,description,created_at,updated_at)
                VALUES(?,?,?,?,?)""",
                (
                    GLOBAL_PROJECT_ID,
                    "组织级通用装备库",
                    "Explicit opt-in GLOBAL equipment library",
                    timestamp,
                    timestamp,
                ),
            )

    def has_record_hash(self, project_id: str, record_hash: str) -> bool:
        with self.connections.connection() as conn:
            return bool(
                conn.execute(
                    "SELECT 1 FROM equipment_entities WHERE project_id=? AND record_hash=?",
                    (project_id, record_hash),
                ).fetchone()
            )

    def upsert_equipment(self, project_id: str, equipment: Dict[str, Any]) -> str:
        """Insert or update one normalized record and return inserted/updated/duplicate."""
        if project_id != str(equipment.get("project_id") or ""):
            raise ValueError("equipment project_id does not match repository scope")
        record_hash = str(equipment.get("record_hash") or "")
        if record_hash and self.has_record_hash(project_id, record_hash):
            return "duplicate"
        equipment_id = str(equipment.get("equipment_id") or "").strip()
        name = str(equipment.get("name") or "").strip()
        if not equipment_id or not name:
            raise ValueError("equipment_id and name are required")
        timestamp = now_iso()
        with self.connections.transaction() as conn:
            existing = conn.execute(
                "SELECT project_id FROM equipment_entities WHERE equipment_id=?",
                (equipment_id,),
            ).fetchone()
            if existing and existing["project_id"] != project_id:
                raise ValueError("equipment_id already belongs to another project")
            conn.execute(
                """INSERT INTO equipment_entities(
                    equipment_id,project_id,name,category,equipment_type,country,attributes_json,
                    document_id,chunk_id,page_no,jsonl_record_no,need_human_confirm,created_at,updated_at,
                    platform_type,roles_json,interfaces_json,constraints_json,simulation_parameters_json,
                    minimum_unit,maximum_unit,source_description,source_file,source_line_no,
                    raw_payload_json,record_hash
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(equipment_id) DO UPDATE SET
                    name=excluded.name,category=excluded.category,equipment_type=excluded.equipment_type,
                    attributes_json=excluded.attributes_json,updated_at=excluded.updated_at,
                    platform_type=excluded.platform_type,roles_json=excluded.roles_json,
                    interfaces_json=excluded.interfaces_json,constraints_json=excluded.constraints_json,
                    simulation_parameters_json=excluded.simulation_parameters_json,
                    minimum_unit=excluded.minimum_unit,maximum_unit=excluded.maximum_unit,
                    source_description=excluded.source_description,source_file=excluded.source_file,
                    source_line_no=excluded.source_line_no,raw_payload_json=excluded.raw_payload_json,
                    record_hash=excluded.record_hash,jsonl_record_no=excluded.jsonl_record_no""",
                (
                    equipment_id,
                    project_id,
                    name,
                    str(equipment.get("category") or ""),
                    str(equipment.get("platform_type") or ""),
                    "",
                    dumps_json(equipment.get("simulation_parameters") or {}),
                    None,
                    None,
                    None,
                    equipment.get("source_line_no"),
                    0,
                    timestamp,
                    timestamp,
                    str(equipment.get("platform_type") or ""),
                    dumps_json(equipment.get("roles") or []),
                    dumps_json(equipment.get("interfaces") or []),
                    dumps_json(equipment.get("constraints") or []),
                    dumps_json(equipment.get("simulation_parameters") or {}),
                    equipment.get("minimum_unit"),
                    equipment.get("maximum_unit"),
                    str(equipment.get("source_description") or ""),
                    str(equipment.get("source_file") or ""),
                    equipment.get("source_line_no"),
                    str(equipment.get("raw_payload_json") or "{}"),
                    record_hash or None,
                ),
            )
            conn.execute(
                "DELETE FROM equipment_aliases WHERE project_id=? AND equipment_id=?",
                (project_id, equipment_id),
            )
            for alias in equipment.get("aliases") or []:
                alias_text = str(alias).strip()
                if not alias_text:
                    continue
                alias_id = "EQA-" + hashlib.sha256(
                    f"{project_id}\x1f{equipment_id}\x1f{alias_text}".encode("utf-8")
                ).hexdigest()[:20]
                conn.execute(
                    """INSERT OR IGNORE INTO equipment_aliases(
                    alias_id,project_id,equipment_id,alias,created_at
                    ) VALUES(?,?,?,?,?)""",
                    (alias_id, project_id, equipment_id, alias_text, timestamp),
                )
            conn.execute(
                "DELETE FROM equipment_capabilities WHERE project_id=? AND equipment_id=?",
                (project_id, equipment_id),
            )
            for capability in equipment.get("capabilities") or []:
                name_value = str(capability.get("name") or "").strip()
                if not name_value:
                    continue
                capability_id = "CAP-" + hashlib.sha256(
                    f"{project_id}\x1f{equipment_id}\x1f{name_value}".encode("utf-8")
                ).hexdigest()[:20]
                conn.execute(
                    """INSERT INTO equipment_capabilities(
                    capability_id,project_id,equipment_id,name,description,capability_type,
                    parameters_json,constraints_json,jsonl_record_no,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        capability_id,
                        project_id,
                        equipment_id,
                        name_value,
                        str(capability.get("description") or ""),
                        str(capability.get("capability_type") or "observed_parameter"),
                        dumps_json(capability.get("parameters") or {}),
                        dumps_json(capability.get("constraints") or []),
                        equipment.get("source_line_no"),
                        timestamp,
                        timestamp,
                    ),
                )
            conn.execute(
                "DELETE FROM equipment_role_mappings WHERE project_id=? AND equipment_id=?",
                (project_id, equipment_id),
            )
            for priority, role in enumerate(equipment.get("roles") or []):
                role_name = str(role).strip()
                if not role_name:
                    continue
                mapping_id = "EQRM-" + hashlib.sha256(
                    f"{project_id}\x1f{equipment_id}\x1f{role_name}".encode("utf-8")
                ).hexdigest()[:20]
                conn.execute(
                    """INSERT INTO equipment_role_mappings(
                    mapping_id,project_id,equipment_id,role_name,capability_ids_json,
                    constraints_json,priority,jsonl_record_no,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        mapping_id,
                        project_id,
                        equipment_id,
                        role_name,
                        dumps_json([]),
                        dumps_json([]),
                        priority,
                        equipment.get("source_line_no"),
                        timestamp,
                        timestamp,
                    ),
                )
        return "updated" if existing else "inserted"

    @staticmethod
    def _decode(row: Any) -> Dict[str, Any]:
        item = dict(row)
        for column, target, default in (
            ("roles_json", "roles", []),
            ("interfaces_json", "interfaces", []),
            ("constraints_json", "constraints", []),
            ("simulation_parameters_json", "simulation_parameters", {}),
            ("raw_payload_json", "raw_payload", {}),
        ):
            item[target] = loads_json(item.get(column), default)
        return item

    def _hydrate(self, conn: Any, row: Any) -> Dict[str, Any]:
        item = self._decode(row)
        item["aliases"] = [
            alias[0]
            for alias in conn.execute(
                "SELECT alias FROM equipment_aliases WHERE project_id=? AND equipment_id=? ORDER BY alias",
                (item["project_id"], item["equipment_id"]),
            )
        ]
        item["role_mappings"] = [
            dict(role)
            for role in conn.execute(
                """SELECT role_name,document_id,chunk_id,page_no,jsonl_record_no
                FROM equipment_role_mappings
                WHERE project_id=? AND equipment_id=? ORDER BY priority,role_name""",
                (item["project_id"], item["equipment_id"]),
            )
        ]
        item["roles"] = list(
            dict.fromkeys(
                [
                    *item.get("roles", []),
                    *(role["role_name"] for role in item["role_mappings"]),
                ]
            )
        )
        item["capabilities"] = [
            dict(capability)
            for capability in conn.execute(
                """SELECT capability_id,name,description,capability_type,parameters_json,
                constraints_json,document_id,chunk_id,page_no,jsonl_record_no
                FROM equipment_capabilities WHERE project_id=? AND equipment_id=? ORDER BY name""",
                (item["project_id"], item["equipment_id"]),
            )
        ]
        for capability in item["capabilities"]:
            capability["parameters"] = loads_json(
                capability.get("parameters_json"), {}
            )
            capability["constraints"] = loads_json(
                capability.get("constraints_json"), []
            )
        return item

    def get_equipment(
        self, project_id: str, equipment_id: str, allow_global: bool = False
    ) -> Optional[Dict[str, Any]]:
        scopes = self._scopes(project_id, allow_global)
        placeholders = ",".join("?" for _ in scopes)
        with self.connections.connection() as conn:
            row = conn.execute(
                f"""SELECT * FROM equipment_entities WHERE equipment_id=?
                AND project_id IN ({placeholders})
                ORDER BY CASE WHEN project_id=? THEN 0 ELSE 1 END LIMIT 1""",
                (equipment_id, *scopes, project_id),
            ).fetchone()
            return self._hydrate(conn, row) if row else None

    def search_by_name_or_alias(
        self,
        project_id: str,
        query: str,
        allow_global: bool = False,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        scopes = self._scopes(project_id, allow_global)
        source = str(query or "").strip()
        if not source:
            return []
        placeholders = ",".join("?" for _ in scopes)
        like = f"%{source}%"
        sql = f"""SELECT e.*,
            CASE WHEN e.name=? THEN 0
                 WHEN EXISTS(SELECT 1 FROM equipment_aliases exact_alias
                    WHERE exact_alias.project_id=e.project_id
                      AND exact_alias.equipment_id=e.equipment_id AND exact_alias.alias=?) THEN 1
                 WHEN e.name LIKE ? THEN 2 ELSE 3 END AS match_rank
            FROM equipment_entities e
            WHERE e.project_id IN ({placeholders})
              AND (e.name=? OR e.name LIKE ? OR EXISTS(
                    SELECT 1 FROM equipment_aliases matched_alias
                    WHERE matched_alias.project_id=e.project_id
                      AND matched_alias.equipment_id=e.equipment_id
                      AND (matched_alias.alias=? OR matched_alias.alias LIKE ?)))
            ORDER BY match_rank, CASE WHEN e.project_id=? THEN 0 ELSE 1 END, e.name
            LIMIT ?"""
        params: Sequence[Any] = (
            source,
            source,
            like,
            *scopes,
            source,
            like,
            source,
            like,
            project_id,
            max(1, limit),
        )
        with self.connections.connection() as conn:
            return [self._hydrate(conn, row) for row in conn.execute(sql, params)]

    def search_by_capability(
        self,
        project_id: str,
        capability: str,
        allow_global: bool = False,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        scopes = self._scopes(project_id, allow_global)
        source = str(capability or "").strip()
        if not source:
            return []
        placeholders = ",".join("?" for _ in scopes)
        with self.connections.connection() as conn:
            rows = conn.execute(
                f"""SELECT DISTINCT e.* FROM equipment_entities e
                JOIN equipment_capabilities c ON c.project_id=e.project_id AND c.equipment_id=e.equipment_id
                WHERE e.project_id IN ({placeholders}) AND (c.name=? OR c.name LIKE ? OR c.description LIKE ?)
                ORDER BY CASE WHEN c.name=? THEN 0 ELSE 1 END,
                         CASE WHEN e.project_id=? THEN 0 ELSE 1 END, e.name LIMIT ?""",
                (*scopes, source, f"%{source}%", f"%{source}%", source, project_id, max(1, limit)),
            )
            return [self._hydrate(conn, row) for row in rows]

    def list_by_category(
        self,
        project_id: str,
        category: str,
        allow_global: bool = False,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        scopes = self._scopes(project_id, allow_global)
        placeholders = ",".join("?" for _ in scopes)
        with self.connections.connection() as conn:
            rows = conn.execute(
                f"""SELECT * FROM equipment_entities WHERE project_id IN ({placeholders})
                AND category=? ORDER BY CASE WHEN project_id=? THEN 0 ELSE 1 END,name LIMIT ?""",
                (*scopes, category, project_id, max(1, limit)),
            )
            return [self._hydrate(conn, row) for row in rows]

    def list_candidates(
        self,
        project_id: str,
        allow_global: bool = False,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """List only the explicitly requested project scopes for structured matching."""
        scopes = self._scopes(project_id, allow_global)
        placeholders = ",".join("?" for _ in scopes)
        with self.connections.connection() as conn:
            rows = conn.execute(
                f"""SELECT * FROM equipment_entities WHERE project_id IN ({placeholders})
                ORDER BY CASE WHEN project_id=? THEN 0 ELSE 1 END,name LIMIT ?""",
                (*scopes, project_id, max(1, limit)),
            )
            return [self._hydrate(conn, row) for row in rows]

    def upsert_inventory(
        self,
        project_id: str,
        equipment_id: str,
        quantity: int,
        status: str = "available",
        configuration: Optional[Dict[str, Any]] = None,
        allow_global: bool = False,
    ) -> str:
        """Set current-project inventory for a local or explicitly referenced GLOBAL item."""
        if quantity < 0:
            raise ValueError("inventory quantity must be non-negative")
        inventory_id = "INV-" + hashlib.sha256(
            f"{project_id}\x1f{equipment_id}".encode("utf-8")
        ).hexdigest()[:20]
        timestamp = now_iso()
        with self.connections.transaction() as conn:
            if not conn.execute(
                "SELECT 1 FROM projects WHERE project_id=?", (project_id,)
            ).fetchone():
                raise ValueError("inventory project does not exist")
            equipment_scopes = [
                project_id, *([GLOBAL_PROJECT_ID] if allow_global else [])
            ]
            placeholders = ",".join("?" for _ in equipment_scopes)
            if not conn.execute(
                f"""SELECT 1 FROM equipment_entities WHERE equipment_id=?
                AND project_id IN ({placeholders})""",
                (equipment_id, *equipment_scopes),
            ).fetchone():
                raise ValueError("inventory equipment does not exist in allowed scope")
            conn.execute(
                """INSERT INTO project_equipment_inventory(
                inventory_id,project_id,equipment_id,quantity,status,configuration_json,
                notes_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(inventory_id) DO UPDATE SET quantity=excluded.quantity,
                status=excluded.status,configuration_json=excluded.configuration_json,
                updated_at=excluded.updated_at""",
                (
                    inventory_id,
                    project_id,
                    equipment_id,
                    quantity,
                    status,
                    dumps_json(configuration or {}),
                    dumps_json([]),
                    timestamp,
                    timestamp,
                ),
            )
        return inventory_id

    def inventory_quantity(self, project_id: str, equipment_id: str) -> int:
        """Return usable inventory in the current project; GLOBAL stock never leaks in."""
        self._scopes(project_id, False)
        with self.connections.connection() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(quantity),0) FROM project_equipment_inventory
                WHERE project_id=? AND equipment_id=? AND status='available'""",
                (project_id, equipment_id),
            ).fetchone()
            return int(row[0] or 0)

    def record_import_error(
        self,
        project_id: str,
        source_file: str,
        source_line_no: int,
        error_type: str,
        error_message: str,
        raw_line: str = "",
    ) -> None:
        with self.connections.transaction() as conn:
            conn.execute(
                "INSERT INTO equipment_import_errors VALUES(?,?,?,?,?,?,?,?)",
                (
                    new_id("EQERR"),
                    project_id,
                    source_file,
                    source_line_no,
                    error_type,
                    error_message,
                    raw_line[:4000],
                    now_iso(),
                ),
            )

    def list_import_errors(
        self, project_id: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        self._scopes(project_id, False)
        with self.connections.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """SELECT * FROM equipment_import_errors WHERE project_id=?
                    ORDER BY created_at DESC,source_line_no LIMIT ?""",
                    (project_id, max(1, limit)),
                )
            ]
