"""Unit tests for bound project retrieval and hybrid scoring."""

from __future__ import annotations

from typing import Dict, List, Optional

import pytest

from domain.exceptions import ProjectScopeError
from infrastructure.retrieval.project_retriever import ProjectRetriever


class FakeEmbeddingProvider:
    model = "fake-embedding"

    def __init__(
        self, vectors: Optional[Dict[str, Optional[List[float]]]] = None
    ) -> None:
        self.vectors = vectors or {}

    def embed(self, text: str) -> Optional[List[float]]:
        return self.vectors.get(text)


class FakeManager:
    def __init__(self, chunks: List[dict]) -> None:
        self.chunks = chunks
        self.embeddings: List[dict] = []

    def list_chunks(self, project_id: str, limit: int = 2000) -> List[dict]:
        return list(self.chunks)[:limit]

    def list_chunk_embeddings(
        self, project_id: str, model_name: str = ""
    ) -> List[dict]:
        return [
            row
            for row in self.embeddings
            if row["project_id"] == project_id
            and (not model_name or row["model_name"] == model_name)
        ]

    def save_chunk_embedding(self, **kwargs) -> None:
        self.embeddings.append({**kwargs, "project_id": kwargs["project_id"]})


def chunk(chunk_id: str, content: str, project_id: str = "P1", **extra) -> dict:
    return {
        "chunk_id": chunk_id,
        "project_id": project_id,
        "document_id": extra.get("document_id", "D1"),
        "filename": extra.get("filename", "requirements.txt"),
        "content": content,
        "chunk_index": extra.get("chunk_index", 1),
        "page_no": extra.get("page_no"),
        "source_type": extra.get("source_type", "txt"),
    }


def test_keyword_retrieval() -> None:
    manager = FakeManager([chunk("C1", "订单系统接收订单"), chunk("C2", "告警系统")])
    rows = ProjectRetriever(manager, "P1", FakeEmbeddingProvider()).search("订单")
    assert [row.chunk_id for row in rows] == ["C1"]
    assert rows[0].retrieval_method == "keyword"


def test_embedding_retrieval() -> None:
    rows = [chunk("C1", "alpha"), chunk("C2", "beta")]
    provider = FakeEmbeddingProvider(
        {"semantic": [1.0, 0.0], "alpha": [1.0, 0.0], "beta": [0.0, 1.0]}
    )
    result = ProjectRetriever(FakeManager(rows), "P1", provider).search("semantic")
    assert result[0].chunk_id == "C1"
    assert result[0].retrieval_method == "embedding"


def test_hybrid_sorting_prefers_semantic_and_keyword_match() -> None:
    rows = [chunk("C1", "订单 alpha"), chunk("C2", "订单 beta")]
    provider = FakeEmbeddingProvider(
        {"订单": [1.0, 0.0], "订单 alpha": [1.0, 0.0], "订单 beta": [0.0, 1.0]}
    )
    result = ProjectRetriever(FakeManager(rows), "P1", provider).search("订单")
    assert [row.chunk_id for row in result] == ["C1", "C2"]
    assert result[0].retrieval_method == "hybrid"


def test_embedding_unavailable_falls_back_to_keyword() -> None:
    manager = FakeManager([chunk("C1", "系统支持订单导入")])
    result = ProjectRetriever(manager, "P1", FakeEmbeddingProvider()).search("订单")
    assert result and result[0].keyword_score > 0
    assert result[0].vector_score == 0


def test_different_project_row_is_rejected() -> None:
    manager = FakeManager([chunk("C1", "订单", project_id="P2")])
    with pytest.raises(ProjectScopeError):
        ProjectRetriever(manager, "P1", FakeEmbeddingProvider()).search("订单")


def test_empty_knowledge_base() -> None:
    assert (
        ProjectRetriever(FakeManager([]), "P1", FakeEmbeddingProvider()).search("订单")
        == []
    )


def test_duplicate_chunks_are_deduplicated() -> None:
    manager = FakeManager([chunk("C1", "订单"), chunk("C1", "订单订单")])
    result = ProjectRetriever(manager, "P1", FakeEmbeddingProvider()).search("订单")
    assert len(result) == 1
    assert result[0].chunk_id == "C1"


def test_source_information_is_preserved() -> None:
    manager = FakeManager(
        [
            chunk(
                "C1",
                "订单",
                document_id="DOC-9",
                filename="spec.pdf",
                page_no=7,
                chunk_index=3,
                source_type="pdf_text",
            )
        ]
    )
    row = ProjectRetriever(manager, "P1", FakeEmbeddingProvider()).search("订单")[0]
    assert row.document_id == "DOC-9"
    assert row.document_name == "spec.pdf"
    assert row.page_number == 7
    assert row.position_metadata == {"chunk_index": 3, "source_type": "pdf_text"}
