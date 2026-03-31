"""Markdown file parser."""

from pathlib import Path

from ..types import ParseResult
from .plaintext import _read_text_with_fallback


class MarkdownParser:
    """Reads markdown files. The text and markdown fields are identical."""

    @property
    def name(self) -> str:
        return "markdown"

    @property
    def supported_extensions(self) -> set[str]:
        return {".md", ".markdown"}

    def available(self) -> bool:
        return True

    async def parse(self, path: Path, options: dict | None = None) -> ParseResult:
        text = _read_text_with_fallback(str(path))
        return ParseResult(text=text, markdown=text)
