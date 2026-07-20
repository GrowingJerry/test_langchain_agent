# -*- coding: utf-8 -*-
"""Deprecated compatibility façade for repository-based project persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import OUTPUT_SQLITE_DIR, OUTPUTS_DIR
from infrastructure.db.connection import SQLiteConnectionManager
from infrastructure.db.migrations import migrate_database
from infrastructure.db.repositories.base import new_id, now_iso
from infrastructure.db.repositories.chunk_repository import ChunkRepository
from infrastructure.db.repositories.document_repository import DocumentRepository
from infrastructure.db.repositories.equipment_repository import EquipmentRepository
from infrastructure.db.repositories.generation_repository import GenerationRepository
from infrastructure.db.repositories.profile_repository import ProfileRepository
from infrastructure.db.repositories.project_repository import ProjectRepository
from infrastructure.db.repositories.requirement_repository import RequirementRepository
from infrastructure.db.repositories.review_repository import ReviewRepository
from infrastructure.db.repositories.scenario_repository import ScenarioRepository
from infrastructure.db.repositories.trace_repository import TraceRepository

DEFAULT_PROJECT_DB = OUTPUT_SQLITE_DIR / "project_workspace.db"
PROJECT_OUTPUT_ROOT = OUTPUTS_DIR / "projects"

__all__ = [
    "DEFAULT_PROJECT_DB",
    "PROJECT_OUTPUT_ROOT",
    "ProjectManager",
    "new_id",
    "now_iso",
]


class ProjectManager:
    """Deprecated façade; prefer entity repositories in new persistence code."""

    def __init__(self, db_path: Path = DEFAULT_PROJECT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        PROJECT_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self.connections = SQLiteConnectionManager(self.db_path)
        migrate_database(self.connections)
        self.projects = ProjectRepository(self.connections)
        self.documents = DocumentRepository(self.connections)
        self.equipment = EquipmentRepository(self.connections)
        self.chunks = ChunkRepository(self.connections)
        self.profiles = ProfileRepository(self.connections)
        self.requirements = RequirementRepository(self.connections)
        self.scenarios = ScenarioRepository(self.connections)
        self.traces = TraceRepository(self.connections)
        self.generations = GenerationRepository(self.connections, self.traces)
        self.reviews = ReviewRepository(self.connections)

    def _connect(self) -> Any:
        """Compatibility escape hatch; callers own and must close the connection."""
        return self.connections.connect()

    def _init_db(self) -> None:
        migrate_database(self.connections)

    def project_dir(self, project_id: str) -> Path:
        path = PROJECT_OUTPUT_ROOT / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def uploads_dir(self, project_id: str) -> Path:
        path = self.project_dir(project_id) / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_project(
        self, project_name: str, description: str = ""
    ) -> Dict[str, Any]:
        row = self.projects.create(project_name, description)
        self.project_dir(row["project_id"])
        return row

    def list_projects(self) -> List[Dict[str, Any]]:
        return self.projects.list()

    def get_project(self, project_id: str) -> Optional[Dict[str, Any]]:
        return self.projects.get(project_id)

    def add_document(
        self,
        project_id: str,
        filename: str,
        file_type: str,
        file_path: Path,
        file_hash: str = "",
        parser_type: str = "",
    ) -> str:
        return self.documents.add(
            project_id, filename, file_type, file_path, file_hash, parser_type
        )

    def get_document_by_hash(
        self, project_id: str, file_hash: str
    ) -> Optional[Dict[str, Any]]:
        return self.documents.get_by_hash(project_id, file_hash)

    def save_document_parse_progress(
        self,
        project_id: str,
        document_id: str,
        report: Dict[str, Any],
        status: str = "processing",
    ) -> None:
        self.documents.save_parse_progress(project_id, document_id, report, status)

    def list_documents(self, project_id: str) -> List[Dict[str, Any]]:
        return self.documents.list(project_id)

    def delete_document(self, project_id: str, document_id: str) -> None:
        self.documents.delete(project_id, document_id)

    def save_project_asset(self, project_id: str, asset_data: Dict[str, Any]) -> str:
        return self.documents.save_asset(project_id, asset_data)

    def list_project_assets(self, project_id: str) -> List[Dict[str, Any]]:
        return self.documents.list_assets(project_id)

    def save_visual_evidence(
        self, project_id: str, asset_id: str, evidence_data: Dict[str, Any]
    ) -> str:
        return self.documents.save_evidence(project_id, asset_id, evidence_data)

    def list_visual_evidence_by_project(self, project_id: str) -> List[Dict[str, Any]]:
        return self.documents.list_evidence(project_id)

    def get_visual_evidence_by_asset(
        self, project_id: str, asset_id: str
    ) -> List[Dict[str, Any]]:
        return self.documents.list_evidence(project_id, asset_id)

    def replace_chunks(
        self, project_id: str, document_id: str, chunks: List[Any]
    ) -> List[str]:
        return self.chunks.replace(project_id, document_id, chunks)

    def append_chunks(
        self,
        project_id: str,
        document_id: str,
        chunks: List[Any],
        parse_report: Optional[Dict[str, Any]] = None,
        processing_status: str = "processing",
    ) -> List[str]:
        return self.chunks.append(
            project_id, document_id, chunks, parse_report, processing_status
        )

    def save_chunk_embedding(
        self,
        project_id: str,
        document_id: str,
        chunk_id: str,
        embedding: List[float],
        model_name: str,
    ) -> None:
        self.chunks.save_embedding(
            project_id, document_id, chunk_id, embedding, model_name
        )

    def list_chunk_embeddings(
        self, project_id: str, model_name: str = ""
    ) -> List[Dict[str, Any]]:
        return self.chunks.list_embeddings(project_id, model_name)

    def list_chunks(self, project_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        return self.chunks.list(project_id, limit)

    def combined_project_text(self, project_id: str, max_chars: int = 24000) -> str:
        return self.chunks.combined_text(project_id, max_chars)

    def save_profile(self, project_id: str, profile: Dict[str, Any]) -> None:
        self.profiles.save(project_id, profile)

    def get_profile(self, project_id: str) -> Optional[Dict[str, Any]]:
        return self.profiles.get(project_id)

    def replace_requirements(self, project_id: str, rows: List[Dict[str, Any]]) -> None:
        self.requirements.replace(project_id, rows)

    def upsert_requirement(self, project_id: str, row: Dict[str, Any]) -> None:
        self.requirements.upsert(project_id, row)

    def list_requirements(self, project_id: str) -> List[Dict[str, Any]]:
        return self.requirements.list(project_id)

    def get_requirement(
        self, project_id: str, requirement_id: str
    ) -> Optional[Dict[str, Any]]:
        return self.requirements.get(project_id, requirement_id)

    def replace_scenario_cards(
        self, project_id: str, requirements: List[Dict[str, Any]]
    ) -> None:
        self.scenarios.replace_basic(project_id, requirements)

    def get_scenario_card(
        self, project_id: str, requirement_id: str
    ) -> Optional[Dict[str, Any]]:
        return self.scenarios.get_for_requirement(project_id, requirement_id)

    def replace_full_scenario_cards(
        self, project_id: str, cards: List[Dict[str, Any]]
    ) -> None:
        self.scenarios.replace_full(project_id, cards)

    def list_scenario_cards(
        self, project_id: str, requirement_id: str = ""
    ) -> List[Dict[str, Any]]:
        return self.scenarios.list(project_id, requirement_id)

    def save_generation_context(
        self,
        project_id: str,
        requirement_id: str,
        case_type: str,
        context: Dict[str, Any],
    ) -> str:
        return self.generations.save_context(
            project_id, requirement_id, case_type, context
        )

    def list_generation_contexts(
        self, project_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        return self.generations.list_contexts(project_id, limit)

    def save_quality_score(
        self, project_id: str, case_id: str, context_id: str, result: Dict[str, Any]
    ) -> str:
        return self.generations.save_quality(project_id, case_id, context_id, result)

    def list_quality_scores(self, project_id: str) -> List[Dict[str, Any]]:
        return self.generations.list_quality(project_id)

    def create_generation_run(
        self,
        project_id: str,
        run_type: str,
        model_name: str = "",
        prompt_snapshot: str = "",
        status: str = "created",
    ) -> str:
        return self.generations.create_run(
            project_id, run_type, model_name, prompt_snapshot, status
        )

    def save_generated_case(
        self,
        project_id: str,
        case_data: Dict[str, Any],
        generation_run_id: str = "",
        source_chunks: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.generations.save_case(
            project_id, case_data, generation_run_id, source_chunks
        )

    def list_generated_cases(self, project_id: str) -> List[Dict[str, Any]]:
        return self.generations.list_cases(project_id)

    def list_trace_sources(self, project_id: str) -> List[Dict[str, Any]]:
        return self.traces.list(project_id)

    def save_review_result(
        self,
        project_id: str,
        case_id: str,
        review_type: str,
        status: str,
        issues: List[str],
    ) -> None:
        self.reviews.save(project_id, case_id, review_type, status, issues)

    def list_review_results(self, project_id: str) -> List[Dict[str, Any]]:
        return self.reviews.list(project_id)
