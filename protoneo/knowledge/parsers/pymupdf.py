"""PyMuPDF-based PDF text extraction (fallback parser)."""

import logging
from pathlib import Path

from ..types import ParseResult

logger = logging.getLogger("protoneo.knowledge.parsers.pymupdf")


class PyMuPDFParser:
    """Extracts plain text from PDFs using PyMuPDF (fitz)."""

    @property
    def name(self) -> str:
        return "pymupdf"

    @property
    def supported_extensions(self) -> set[str]:
        return {".pdf"}

    def available(self) -> bool:
        try:
            import fitz  # noqa: F401
            return True
        except ImportError:
            return False

    async def parse(self, path: Path, options: dict | None = None) -> ParseResult:
        import fitz

        parts: list[str] = []
        with fitz.open(str(path)) as doc:
            for page in doc:
                text = page.get_text()
                if text.strip():
                    parts.append(text)
        text = "\n\n".join(parts)
        return ParseResult(text=text)
