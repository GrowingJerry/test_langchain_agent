"""Two-stage candidate retrieval and grounded knowledge persistence."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from chains.knowledge_extraction import KnowledgeExtractionChain
from domain.schemas.knowledge import ExtractedKnowledgeUnit
from domain.schemas.learning import KnowledgeCoverageCandidate, LearningTask
from infrastructure.db.json_codec import dumps_json, loads_json
from infrastructure.db.repositories.base import new_id, now_iso

MAX_EXTRACTION_CANDIDATES = 6


def _terms(task: LearningTask) -> List[str]:
    values = [
        task.learning_goal,
        task.domain,
        task.simulation_object,
        *task.target_subsystems,
        *task.target_topics,
        *task.expected_scenario_types,
    ]
    tokens: List[str] = []
    for value in values:
        normalized = str(value).strip().lower()
        if normalized:
            tokens.append(normalized)
            tokens.extend(re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", normalized))
    return list(dict.fromkeys(tokens))


class KnowledgeExtractor:
    def __init__(self, manager: Any, chain: KnowledgeExtractionChain) -> None:
        self.manager = manager
        self.chain = chain
        self.last_extraction_errors: List[str] = []

    def retrieve_candidates(
        self, task: LearningTask, limit: int = 60
    ) -> List[KnowledgeCoverageCandidate]:
        selected = set(task.document_ids)
        excluded = [item.lower() for item in task.excluded_topics if item.strip()]
        terms = _terms(task)
        rows = [
            row for row in self.manager.list_chunks(task.project_id, limit=100000)
            if row.get("document_id") in selected
        ]
        parents = {
            str(row.get("parent_section_id") or ""): row
            for row in rows if row.get("chunk_level") == "parent"
        }
        ranked: List[KnowledgeCoverageCandidate] = []
        for row in rows:
            if row.get("chunk_level") == "parent":
                continue
            content = str(row.get("content") or row.get("chunk_text") or "")
            lowered = content.lower()
            if any(term in lowered for term in excluded):
                continue
            matched = [term for term in terms if term in lowered]
            parent = parents.get(str(row.get("parent_section_id") or ""), {})
            parent_text = str(parent.get("content") or "").lower()
            section_matches = [term for term in terms if term in parent_text]
            score = float(len(matched) * 2 + len(section_matches))
            if not score:
                continue
            ranked.append(
                KnowledgeCoverageCandidate(
                    document_id=str(row["document_id"]),
                    chunk_id=str(row["chunk_id"]),
                    parent_section_id=str(row.get("parent_section_id") or ""),
                    section_title=str(parent.get("section_title") or ""),
                    page_start=row.get("page_start") or row.get("page_no"),
                    page_end=row.get("page_end") or row.get("page_no"),
                    content=content,
                    matched_topics=list(dict.fromkeys([*matched, *section_matches])),
                    relevance_score=score,
                )
            )
        ranked.sort(key=lambda item: (-item.relevance_score, item.document_id, item.page_start or 0, item.chunk_id))
        return ranked[:limit]

    def extract_and_persist(
        self, task: LearningTask, candidates: List[KnowledgeCoverageCandidate]
    ) -> List[Dict[str, Any]]:
        # The first stage retains the complete coverage list. The structured
        # extraction stage uses a deterministic top-ranked subset so local
        # models cannot overflow their bounded JSON response.
        candidate_rows = [
            item.model_dump(mode="json")
            for item in candidates[:MAX_EXTRACTION_CANDIDATES]
        ]
        output = self.chain.run(
            learning_goal=task.learning_goal,
            domain=task.domain,
            simulation_object=task.simulation_object,
            candidates=candidate_rows,
        )
        sources = {item.chunk_id: item for item in candidates}
        saved: List[Dict[str, Any]] = []
        self.last_extraction_errors = []
        for extracted in output.knowledge_units:
            if extracted.knowledge_type == "parameter" and extracted.parameter is None:
                # Small local models sometimes classify formulas, mappings or
                # rules as parameters without emitting ParameterKnowledge. The
                # source text is still useful and contains no approved numeric
                # value, so preserve it under a deterministic non-parameter
                # type instead of losing the entire extraction batch.
                recovered_type = (
                    "simulation_model"
                    if any(marker in extracted.content for marker in ("=", "±", "π", "公式"))
                    else "constraint"
                )
                self.last_extraction_errors.append(
                    f"知识 {extracted.title!r} 缺少参数结构，已降级为{recovered_type}候选"
                )
                extracted = extracted.model_copy(
                    update={"knowledge_type": recovered_type}
                )
            source_ids = [item for item in extracted.source_chunk_ids if item in sources]
            if not source_ids:
                self.last_extraction_errors.append(
                    f"知识 {extracted.title!r} 缺少有效来源片段"
                )
                continue
            try:
                saved.append(self._save_unit(task, extracted, source_ids, sources))
            except ValueError as exc:
                self.last_extraction_errors.append(str(exc))
        if not saved:
            raise ValueError(
                "结构化抽取没有可保存的有效知识："
                + "；".join(self.last_extraction_errors)
            )
        return saved

    def _save_unit(
        self,
        task: LearningTask,
        unit: ExtractedKnowledgeUnit,
        source_ids: List[str],
        sources: Dict[str, KnowledgeCoverageCandidate],
    ) -> Dict[str, Any]:
        source = sources[source_ids[0]]
        normalized = dict(unit.normalized_data)
        needs_review = unit.confidence < 0.8
        source_page_valid = unit.source_page is None or any(
            item.page_start is not None
            and item.page_end is not None
            and item.page_start <= unit.source_page <= item.page_end
            for item in (sources[source_id] for source_id in source_ids)
        )
        if not source_page_valid:
            needs_review = True
        if unit.knowledge_type == "parameter":
            if unit.parameter is None:
                raise ValueError(f"参数知识 {unit.title!r} 缺少参数结构")
            parameter = unit.parameter.model_dump(mode="json")
            normalized.update(parameter)
            source_text = " ".join(sources[item].content for item in source_ids)
            project_specific = bool(
                task.simulation_object
                and task.simulation_object.lower() in source_text.lower()
            )
            if not parameter.get("unit") or (
                parameter.get("value_type") == "项目值" and not project_specific
            ) or parameter.get("value_type") != "项目值":
                needs_review = True
            normalized["project_specific"] = project_specific
        status = "draft" if needs_review or unit.knowledge_type == "parameter" else "reviewed"
        knowledge_id = new_id("KNU")
        row = {
            "knowledge_unit_id": knowledge_id,
            "project_id": task.project_id,
            "knowledge_type": unit.knowledge_type,
            "title": unit.title,
            "content": unit.content,
            "normalized_data": normalized,
            "tags": unit.tags,
            "document_id": source.document_id,
            "chunk_ids": source_ids,
            "page_no": unit.source_page if source_page_valid and unit.source_page else source.page_start,
            "confidence": unit.confidence,
            "need_human_confirm": needs_review,
            "applicable_conditions": unit.applicable_conditions,
            "inapplicable_conditions": unit.inapplicable_conditions,
            "section_scope": source.section_title or source.parent_section_id,
            "status": status,
            "source_kind": "book",
        }
        with self.manager.connections.transaction() as conn:
            conn.execute(
                """INSERT INTO knowledge_units(
                knowledge_unit_id,project_id,knowledge_type,title,content,
                normalized_data_json,tags_json,document_id,chunk_id,page_no,
                confidence,need_human_confirm,status,created_at,updated_at,
                applicable_conditions_json,inapplicable_conditions_json,
                section_scope,learning_task_id,source_kind,priority
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    knowledge_id, task.project_id, unit.knowledge_type, unit.title,
                    unit.content, dumps_json(normalized), dumps_json(unit.tags),
                    source.document_id, source_ids[0], row["page_no"], unit.confidence,
                    int(needs_review), status, now_iso(), now_iso(),
                    dumps_json(unit.applicable_conditions),
                    dumps_json(unit.inapplicable_conditions), row["section_scope"], task.task_id,
                    "book", 200,
                ),
            )
        return row

    def list_task_units(self, project_id: str, task_id: str) -> List[Dict[str, Any]]:
        with self.manager.connections.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM knowledge_units WHERE project_id=? AND learning_task_id=? ORDER BY created_at",
                (project_id, task_id),
            )
            result = []
            for raw in rows:
                row = dict(raw)
                row["normalized_data"] = loads_json(row.get("normalized_data_json"), {})
                row["tags"] = loads_json(row.get("tags_json"), [])
                row["applicable_conditions"] = loads_json(row.get("applicable_conditions_json"), [])
                row["inapplicable_conditions"] = loads_json(row.get("inapplicable_conditions_json"), [])
                result.append(row)
            return result
