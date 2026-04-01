"""Knowledge subsystem: document processing, graph extraction, ontology."""

import re

from .parser import parse_file, parse_files
from .chunker import chunk_text
from .processor import DocumentProcessor
from .parsers import PlainTextParser, MarkdownParser, DoclingParser

__all__ = [
    "parse_file",
    "parse_files",
    "chunk_text",
    "DocumentProcessor",
    "create_document_processor",
]

# Bare line numbers from two-column PDF layouts
_BARE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")


def _strip_line_number_pollution(text: str) -> str:
    """Remove leading/trailing runs of bare line numbers from PDF extraction."""
    lines = text.split("\n")
    start = 0
    while start < len(lines) and _BARE_NUMBER_RE.match(lines[start]):
        start += 1
    end = len(lines)
    while end > start and _BARE_NUMBER_RE.match(lines[end - 1]):
        end -= 1
    return "\n".join(lines[start:end])


def create_document_processor() -> DocumentProcessor:
    """Create a DocumentProcessor with all built-in parsers registered."""
    proc = DocumentProcessor()
    proc.register_parser(PlainTextParser(), priority=0)
    proc.register_parser(MarkdownParser(), priority=0)
    proc.register_parser(DoclingParser(), priority=20)
    proc.register_post_processor(_strip_line_number_pollution)
    return proc
