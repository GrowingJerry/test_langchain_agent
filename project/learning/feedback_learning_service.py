"""Human-reviewed feedback learning without mutating formal project knowledge."""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from infrastructure.db.json_codec import dumps_json, loads_json
from infrastructure.db.repositories.base import new_id, now_iso

FeedbackType = Literal[
    "scenario_completion_rule", "role_mapping_rule", "equipment_preference_rule",
    "parameter_usage_rule", "validation_rule", "writing_style_rule",
]
ENTITY_TYPES = {"scenario", "test_case"}
NUMERIC_FIELDS = {"quantity", "value", "valid_range", "minimum", "maximum", "threshold"}


class FeedbackCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    original_value: Any = None
    corrected_value: Any = None
    field_name: str
    entity_type: str
    correction_reason: str = ""
    project_id: str
    scenario_id: str = ""
    requirement_ids: List[str] = Field(default_factory=list)
    source_context: Dict[str, Any] = Field(default_factory=dict)
    created_by: str
    status: str = "candidate"
    candidate_type: FeedbackType | None = None

    @field_validator("field_name", "project_id", "created_by")
    @classmethod
    def non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be empty")
        return value.strip()

    @field_validator("entity_type")
    @classmethod
    def valid_entity(cls, value: str) -> str:
        if value not in ENTITY_TYPES:
            raise ValueError("entity_type must be scenario or test_case")
        return value


class FeedbackLearningService:
    """Record corrections, aggregate candidates, and expose approved scoped rules."""

    def __init__(self, manager: Any) -> None:
        self.manager = manager

    def record_correction(self, value: FeedbackCorrection | Dict[str, Any]) -> Dict[str, Any]:
        correction = value if isinstance(value, FeedbackCorrection) else FeedbackCorrection.model_validate(value)
        candidate_type = correction.candidate_type or self._infer_type(correction)
        context = self._sanitized_context(correction.source_context)
        key_payload = {
            "entity_type": correction.entity_type, "field_name": correction.field_name,
            "candidate_type": candidate_type, "corrected_value": correction.corrected_value,
            "condition": context.get("condition", {}),
        }
        candidate_key = hashlib.sha256(
            dumps_json(key_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        timestamp = now_iso()
        correction_id = new_id("FBC")
        with self.manager.connections.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM feedback_candidates WHERE project_id=? AND candidate_key=?",
                (correction.project_id, candidate_key),
            ).fetchone()
            if existing:
                candidate_id = str(existing["candidate_id"])
                payload = loads_json(existing["feedback_json"], {})
                correction_ids = list(payload.get("correction_ids") or [])
                correction_ids.append(correction_id)
                payload.update({"correction_ids": correction_ids,
                                "latest_corrected_value": correction.corrected_value,
                                "evidence_count": len(correction_ids)})
                conn.execute(
                    """UPDATE feedback_candidates SET feedback_json=?,occurrence_count=?,updated_at=?
                    WHERE project_id=? AND candidate_id=?""",
                    (dumps_json(payload), len(correction_ids), timestamp,
                     correction.project_id, candidate_id),
                )
            else:
                candidate_id = new_id("FCD")
                payload = {
                    "field_name": correction.field_name,
                    "entity_type": correction.entity_type,
                    "correction_ids": [correction_id],
                    "evidence_count": 1,
                    "latest_corrected_value": correction.corrected_value,
                    "condition": context.get("condition", {}),
                    "action": {"field_name": correction.field_name,
                               "value": correction.corrected_value},
                    "numeric_parameter": self._is_numeric(correction),
                }
                conn.execute(
                    """INSERT INTO feedback_candidates(
                    candidate_id,project_id,candidate_type,target_artifact_type,
                    target_artifact_id,feedback_json,status,created_at,updated_at,
                    candidate_key,occurrence_count) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (candidate_id, correction.project_id, candidate_type,
                     correction.entity_type, correction.scenario_id or None,
                     dumps_json(payload), "pending", timestamp, timestamp, candidate_key, 1),
                )
            conn.execute(
                """INSERT INTO feedback_corrections(
                correction_id,project_id,original_value_json,corrected_value_json,
                field_name,entity_type,correction_reason,scenario_id,requirement_ids_json,
                source_context_json,created_by,status,candidate_id,created_at,
                document_id,chunk_id,page_no,jsonl_record_no)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (correction_id, correction.project_id, dumps_json(correction.original_value),
                 dumps_json(correction.corrected_value), correction.field_name,
                 correction.entity_type, correction.correction_reason,
                 correction.scenario_id or None, dumps_json(correction.requirement_ids),
                 dumps_json(context), correction.created_by, "candidate", candidate_id,
                 timestamp, context.get("document_id"), context.get("chunk_id"),
                 context.get("page_no"), context.get("jsonl_record_no")),
            )
        return {"correction_id": correction_id, "candidate_id": candidate_id,
                "candidate_type": candidate_type, "status": "pending"}

    def approve_candidate(
        self, project_id: str, candidate_id: str, *, approved_by: str,
        promote_to_global: bool = False, name: str = "",
    ) -> Dict[str, Any]:
        if not approved_by.strip():
            raise ValueError("approved_by is required")
        timestamp = now_iso()
        with self.manager.connections.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM feedback_candidates WHERE project_id=? AND candidate_id=?",
                (project_id, candidate_id),
            ).fetchone()
            if not row:
                raise ValueError("feedback candidate was not found in the bound project")
            payload = loads_json(row["feedback_json"], {})
            if promote_to_global and payload.get("numeric_parameter"):
                raise ValueError("numeric parameter feedback cannot be promoted to GLOBAL")
            scope = "GLOBAL" if promote_to_global else project_id
            if promote_to_global:
                conn.execute(
                    """INSERT OR IGNORE INTO projects(
                    project_id,project_name,description,created_at,updated_at)
                    VALUES('GLOBAL','Organization shared scope',?,?,?)""",
                    ("Explicitly promoted organization-level data", timestamp, timestamp),
                )
            rule_id = new_id("FLR")
            conn.execute(
                """INSERT INTO approved_learning_rules(
                learning_rule_id,project_id,name,rule_type,condition_json,action_json,
                source_feedback_candidate_id,enabled,approved_by,approved_at,created_at)
                VALUES(?,?,?,?,?,?,?,1,?,?,?)""",
                (rule_id, scope, name.strip() or f"feedback:{row['candidate_type']}",
                 row["candidate_type"], dumps_json(payload.get("condition", {})),
                 dumps_json(payload.get("action", {})), candidate_id,
                 approved_by.strip(), timestamp, timestamp),
            )
            conn.execute(
                "UPDATE feedback_candidates SET status='approved',updated_at=? WHERE candidate_id=?",
                (timestamp, candidate_id),
            )
            conn.execute(
                "UPDATE feedback_corrections SET status='approved' WHERE project_id=? AND candidate_id=?",
                (project_id, candidate_id),
            )
            self._audit(conn, scope, rule_id, "approved", approved_by, "", {
                "candidate_id": candidate_id, "promoted_to_global": promote_to_global,
            })
        return {"learning_rule_id": rule_id, "project_id": scope,
                "rule_type": row["candidate_type"], "enabled": True}

    def revoke_rule(self, project_id: str, rule_id: str, *, revoked_by: str,
                    reason: str = "") -> Dict[str, Any]:
        if not revoked_by.strip():
            raise ValueError("revoked_by is required")
        timestamp = now_iso()
        with self.manager.connections.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM approved_learning_rules WHERE project_id=? AND learning_rule_id=?",
                (project_id, rule_id),
            ).fetchone()
            if not row:
                raise ValueError("approved feedback rule was not found in the bound scope")
            conn.execute(
                """UPDATE approved_learning_rules SET enabled=0,revoked_by=?,revoked_at=?,
                revoke_reason=? WHERE project_id=? AND learning_rule_id=?""",
                (revoked_by, timestamp, reason, project_id, rule_id),
            )
            self._audit(conn, project_id, rule_id, "revoked", revoked_by, reason, {})
        return {"learning_rule_id": rule_id, "project_id": project_id, "enabled": False}

    def match_approved_rules(
        self, project_id: str, context: Dict[str, Any], *, allow_global: bool = False,
    ) -> List[Dict[str, Any]]:
        scopes = [project_id, *(["GLOBAL"] if allow_global else [])]
        placeholders = ",".join("?" for _ in scopes)
        with self.manager.connections.connection() as conn:
            rows = [dict(row) for row in conn.execute(
                f"""SELECT * FROM approved_learning_rules
                WHERE project_id IN ({placeholders}) AND enabled=1
                ORDER BY CASE WHEN project_id=? THEN 0 ELSE 1 END,approved_at,learning_rule_id""",
                (*scopes, project_id),
            )]
        matched = []
        for row in rows:
            condition = loads_json(row.get("condition_json"), {})
            reasons = self._condition_reasons(condition, context)
            if reasons is None:
                continue
            matched.append({
                "learning_rule_id": row["learning_rule_id"], "project_id": row["project_id"],
                "rule_type": row["rule_type"], "name": row["name"],
                "condition": condition, "action": loads_json(row.get("action_json"), {}),
                "match_reason": "；".join(reasons) if reasons else "无附加条件，作用域匹配",
                "source_feedback_candidate_id": row.get("source_feedback_candidate_id"),
            })
        return matched

    def explain_generation(self, project_id: str, rule_ids: List[str],
                           *, allow_global: bool = False) -> List[Dict[str, Any]]:
        if not rule_ids:
            return []
        scopes = [project_id, *(["GLOBAL"] if allow_global else [])]
        placeholders = ",".join("?" for _ in scopes)
        ids = ",".join("?" for _ in rule_ids)
        with self.manager.connections.connection() as conn:
            rows = [dict(row) for row in conn.execute(
                f"""SELECT * FROM approved_learning_rules WHERE project_id IN ({placeholders})
                AND learning_rule_id IN ({ids}) AND enabled=1""", (*scopes, *rule_ids),
            )]
        return [{"learning_rule_id": row["learning_rule_id"], "name": row["name"],
                 "rule_type": row["rule_type"], "project_id": row["project_id"],
                 "condition": loads_json(row["condition_json"], {}),
                 "action": loads_json(row["action_json"], {}),
                 "why": "人工批准的反馈规则，且本次生成条件与作用域匹配"} for row in rows]

    def list_candidates(self, project_id: str, status: str = "") -> List[Dict[str, Any]]:
        query = "SELECT * FROM feedback_candidates WHERE project_id=?"
        params: list[Any] = [project_id]
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY updated_at DESC,candidate_id"
        with self.manager.connections.connection() as conn:
            return [dict(row) | {"feedback": loads_json(row["feedback_json"], {})}
                    for row in conn.execute(query, tuple(params))]

    @staticmethod
    def _infer_type(correction: FeedbackCorrection) -> FeedbackType:
        field = correction.field_name.casefold()
        if any(token in field for token in ("equipment", "装备")):
            return "equipment_preference_rule"
        if any(token in field for token in ("role", "角色")):
            return "role_mapping_rule"
        if FeedbackLearningService._is_numeric(correction):
            return "parameter_usage_rule"
        if any(token in field for token in ("validation", "expected", "校验", "预期")):
            return "validation_rule"
        if correction.entity_type == "test_case" and any(
            token in field for token in ("title", "description", "名称", "描述")
        ):
            return "writing_style_rule"
        return "scenario_completion_rule"

    @staticmethod
    def _is_numeric(correction: FeedbackCorrection) -> bool:
        field = correction.field_name.casefold()
        return isinstance(correction.corrected_value, (int, float)) or any(
            token in field for token in NUMERIC_FIELDS
        )

    @staticmethod
    def _sanitized_context(value: Dict[str, Any]) -> Dict[str, Any]:
        forbidden = {"conversation", "conversation_history", "messages", "chat_history"}
        return {key: item for key, item in value.items() if key not in forbidden}

    @staticmethod
    def _condition_reasons(condition: Dict[str, Any], context: Dict[str, Any]) -> List[str] | None:
        reasons = []
        for key, expected in condition.items():
            actual = context.get(key)
            if isinstance(expected, list):
                actual_values = actual if isinstance(actual, list) else [actual]
                if not set(map(str, expected)) & set(map(str, actual_values)):
                    return None
            elif str(actual or "").casefold() != str(expected).casefold():
                return None
            reasons.append(f"{key}={expected}")
        return reasons

    @staticmethod
    def _audit(conn: Any, project_id: str, rule_id: str, action: str,
               actor: str, reason: str, details: Dict[str, Any]) -> None:
        conn.execute(
            """INSERT INTO feedback_rule_audit(audit_id,project_id,learning_rule_id,
            action,actor,reason,details_json,created_at) VALUES(?,?,?,?,?,?,?,?)""",
            (new_id("FRA"), project_id, rule_id, action, actor.strip(), reason,
             dumps_json(details), now_iso()),
        )
