"""Optional, page-streaming document parser adapters."""

from infrastructure.documents.parsers.base import DocumentParser, ParsedPage
from infrastructure.documents.parsers.parser_router import ParserRouter

__all__ = ["DocumentParser", "ParsedPage", "ParserRouter"]

