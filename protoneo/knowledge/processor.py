"""Document processor with registry and fallback chain."""

import logging
from pathlib import Path
from typing import Callable

from ..agents.types import Document
from .types import ParseResult, Parser

logger = logging.getLogger("protoneo.knowledge.processor")


class DocumentProcessor:
    """Registry of parsers with priority-based fallback chains.

    Higher priority parsers are tried first. If a parser fails or is
    unavailable, the next one in priority order is tried.
    """

    def __init__(self):
        self._parsers: list[tuple[int, Parser]] = []
        self._post_processors: list[Callable[[str], str]] = []

    def register_parser(self, parser: Parser, priority: int = 0) -> None:
        """Register a parser. Higher priority = tried first."""
        self._parsers.append((priority, parser))
        self._parsers.sort(key=lambda t: t[0], reverse=True)

    def register_post_processor(self, fn: Callable[[str], str]) -> None:
        """Register a text cleanup function applied after parsing."""
        self._post_processors.append(fn)

    def available_parsers(self, extension: str) -> list[str]:
        """List available parser names for a file extension."""
        ext = extension.lower() if extension.startswith(".") else f".{extension.lower()}"
        return [
            p.name
            for _, p in self._parsers
            if ext in p.supported_extensions and p.available()
        ]

    async def process(
        self,
        path: Path,
        preferred_parser: str | None = None,
    ) -> Document:
        """Process a file into a Document.

        Tries parsers in priority order. If preferred_parser is set,
        tries that one first. Falls back through the chain on failure.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        ext = path.suffix.lower()

        # Build candidate list
        candidates: list[Parser] = []
        if preferred_parser:
            for _, p in self._parsers:
                if p.name == preferred_parser and ext in p.supported_extensions and p.available():
                    candidates.append(p)
                    break

        for _, p in self._parsers:
            if ext in p.supported_extensions and p.available() and p not in candidates:
                candidates.append(p)

        if not candidates:
            raise ValueError(f"No parser available for extension: {ext}")

        # Try each candidate
        last_error: Exception | None = None
        for parser in candidates:
            try:
                logger.info("Trying parser '%s' for %s", parser.name, path.name)
                result = await parser.parse(path)
                text = result.text
                for pp in self._post_processors:
                    text = pp(text)
                return Document(
                    document_id=path.stem,
                    filename=path.name,
                    text=text,
                    markdown=result.markdown,
                )
            except Exception as e:
                logger.warning("Parser '%s' failed for %s: %s", parser.name, path.name, e)
                last_error = e

        raise RuntimeError(
            f"All parsers failed for {path.name}. Last error: {last_error}"
        )
