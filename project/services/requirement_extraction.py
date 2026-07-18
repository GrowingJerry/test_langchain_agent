"""Application service preserving rule extraction with an optional future enhancer."""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from core.requirement_extractor import extract_requirements_from_chunks


RequirementEnhancer = Callable[
    [List[Dict[str, str]], List[Dict[str, Any]]], List[Dict[str, str]]
]


class RequirementExtractionService:
    """Run the existing deterministic extractor and optionally enhance its output."""

    def __init__(self, enhancer: Optional[RequirementEnhancer] = None) -> None:
        self.enhancer = enhancer

    def extract(
        self,
        chunks: List[Dict[str, Any]],
        use_enhancement: bool = False,
    ) -> List[Dict[str, str]]:
        rows = extract_requirements_from_chunks(chunks)
        if use_enhancement and self.enhancer is not None:
            return self.enhancer(rows, chunks)
        return rows
