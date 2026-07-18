"""Runtime context that binds an Agent and all tools to one project."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from config.settings import Settings, settings as default_settings
from domain.exceptions import ProjectScopeError
from infrastructure.retrieval.project_retriever import ProjectRetriever


@dataclass
class AgentRuntimeContext:
    """Project-bound dependencies and non-persistent execution observations."""

    project_id: str
    manager: Any
    case_library: Optional[Any] = None
    settings: Settings = default_settings
    retriever: Optional[ProjectRetriever] = None
    used_tool_names: List[str] = field(default_factory=list, init=False)
    retrieved_source_chunk_ids: List[str] = field(default_factory=list, init=False)
    retrieved_source_documents: List[str] = field(default_factory=list, init=False)
    retrieved_scenario_ids: List[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.project_id = str(self.project_id or "").strip()
        if not self.project_id:
            raise ProjectScopeError("AgentRuntimeContext requires a bound project_id")
        if self.retriever is None:
            self.retriever = ProjectRetriever(self.manager, self.project_id)
        elif self.retriever.project_id != self.project_id:
            raise ProjectScopeError(
                "Retriever project scope does not match AgentRuntimeContext"
            )

    def record_tool(self, tool_name: str) -> None:
        if tool_name not in self.used_tool_names:
            self.used_tool_names.append(tool_name)

    def record_chunks(self, chunk_ids: List[str]) -> None:
        for chunk_id in chunk_ids:
            if chunk_id and chunk_id not in self.retrieved_source_chunk_ids:
                self.retrieved_source_chunk_ids.append(chunk_id)

    def record_documents(self, document_names: List[str]) -> None:
        for document_name in document_names:
            if document_name and document_name not in self.retrieved_source_documents:
                self.retrieved_source_documents.append(document_name)

    def record_scenarios(self, scenario_ids: List[str]) -> None:
        for scenario_id in scenario_ids:
            if scenario_id and scenario_id not in self.retrieved_scenario_ids:
                self.retrieved_scenario_ids.append(scenario_id)

    def reset_observations(self) -> None:
        self.used_tool_names.clear()
        self.retrieved_source_chunk_ids.clear()
        self.retrieved_source_documents.clear()
        self.retrieved_scenario_ids.clear()
