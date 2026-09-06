"""Project-scoped retrieval result schemas."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class ProjectChunkResult(BaseModel):
    """One normalized, project-scoped chunk retrieval result."""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    project_id: str
    document_id: str
    document_name: str
    content: str
    score: float
    retrieval_method: str
    page_number: Optional[int] = None
    position_metadata: Dict[str, Any] = Field(default_factory=dict)
    keyword_score: float = 0.0
    vector_score: float = 0.0

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Return a compatibility dictionary for existing project KB callers."""
        data = self.model_dump()
        data.update(
            {
                "filename": self.document_name,
                "page_no": self.page_number,
                "chunk_index": self.position_metadata.get("chunk_index"),
                "source_type": self.position_metadata.get("source_type", ""),
                "final_score": self.score,
                "snippet": self.content[:360]
                + ("..." if len(self.content) > 360 else ""),
            }
        )
        return data
