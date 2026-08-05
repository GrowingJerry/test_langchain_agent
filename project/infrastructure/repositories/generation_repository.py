"""Generation run, context, case, and quality persistence."""

from typing import Any, Dict, List, Optional

from infrastructure.database.json_codec import dumps_json, loads_json
from infrastructure.repositories.base import BaseRepository, new_id, now_iso
from infrastructure.repositories.trace_repository import TraceRepository


class GenerationRepository(BaseRepository):
    def __init__(self, connections: Any, traces: TraceRepository) -> None:
        super().__init__(connections)
        self.traces = traces

    def save_context(
        self,
        project_id: str,
        requirement_id: str,
        case_type: str,
        context: Dict[str, Any],
    ) -> str:
        context_id = new_id("CTX")
        with self.connections.transaction() as conn:
            conn.execute(
                "INSERT INTO case_generation_contexts VALUES(?,?,?,?,?,?)",
                (
                    context_id,
                    project_id,
                    requirement_id,
                    case_type,
                    dumps_json(context),
                    now_iso(),
                ),
            )
        return context_id

    def list_contexts(self, project_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        with self.connections.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM case_generation_contexts WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                    (project_id, limit),
                )
            ]

    def save_quality(
        self, project_id: str, case_id: str, context_id: str, result: Dict[str, Any]
    ) -> str:
        score_id = new_id("QAS")
        with self.connections.transaction() as conn:
            conn.execute(
                "INSERT INTO case_quality_scores VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    score_id,
                    project_id,
                    case_id,
                    context_id,
                    float(result.get("score") or 0),
                    dumps_json(result.get("dimensions", {})),
                    dumps_json(result.get("issues", [])),
                    dumps_json(result.get("suggestions", [])),
                    now_iso(),
                ),
            )
        return score_id

    def list_quality(self, project_id: str) -> List[Dict[str, Any]]:
        with self.connections.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM case_quality_scores WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        result = []
        for source in rows:
            item = dict(source)
            for column, target, default in (
                ("dimensions_json", "dimensions", {}),
                ("issues_json", "issues", []),
                ("suggestions_json", "suggestions", []),
            ):
                item[target] = loads_json(item.get(column), default)
            result.append(item)
        return result

    def create_run(
        self,
        project_id: str,
        run_type: str,
        model_name: str = "",
        prompt_snapshot: str = "",
        status: str = "created",
        metadata: Dict[str, Any] | None = None,
    ) -> str:
        run_id = new_id("RUN")
        with self.connections.transaction() as conn:
            conn.execute(
                """INSERT INTO generation_runs(
                run_id,project_id,run_type,status,model_name,prompt_snapshot,created_at,metadata_json
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    project_id,
                    run_type,
                    status,
                    model_name,
                    prompt_snapshot,
                    now_iso(),
                    dumps_json(metadata or {}),
                ),
            )
        return run_id

    def save_case(
        self,
        project_id: str,
        case_data: Dict[str, Any],
        generation_run_id: str = "",
        source_chunks: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        case_id = str(
            case_data.get("case_id") or case_data.get("用例编号") or ""
        ).strip() or new_id("TC")
        case_data["case_id"] = case_id
        requirement_id = str(
            case_data.get("requirement_id") or case_data.get("需求编号") or ""
        ).strip()
        case_type = str(
            case_data.get("case_type") or case_data.get("测试类别") or ""
        ).strip()
        chunk_ids = case_data.get("source_chunk_ids") or case_data.get("来源片段") or []
        if isinstance(chunk_ids, str):
            chunk_ids = [part.strip() for part in chunk_ids.split(",") if part.strip()]
        chunks = source_chunks or []
        if not chunk_ids and chunks:
            chunk_ids = [
                str(row.get("chunk_id") or "") for row in chunks if row.get("chunk_id")
            ]
            case_data["source_chunk_ids"] = chunk_ids
        with self.connections.transaction() as conn:
            conn.execute(
                """INSERT INTO generated_cases VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(project_id,case_id) DO UPDATE SET
                requirement_id=excluded.requirement_id,case_type=excluded.case_type,source_chunk_ids=excluded.source_chunk_ids,
                generation_run_id=excluded.generation_run_id,case_json=excluded.case_json,created_at=excluded.created_at""",
                (
                    case_id,
                    project_id,
                    requirement_id,
                    case_type,
                    dumps_json(chunk_ids),
                    generation_run_id,
                    dumps_json(case_data),
                    now_iso(),
                ),
            )
            if source_chunks is not None:
                self.traces.replace_for_case(conn, project_id, case_id, chunks)

    def list_cases(self, project_id: str) -> List[Dict[str, Any]]:
        with self.connections.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM generated_cases WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["case_json"] = loads_json(item.get("case_json"), {})
            result.append(item)
        return result
