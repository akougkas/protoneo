"""
Document parsing. Reuses the proven extraction logic from the legacy codebase.
"""

import logging
import uuid
from pathlib import Path

from ..agents.types import Document

logger = logging.getLogger("protoneo.knowledge.parser")

SUPPORTED_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt"}


def _read_text_with_fallback(file_path: str) -> str:
    data = Path(file_path).read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass

    encoding = None
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(data).best()
        if best and best.encoding:
            encoding = best.encoding
    except Exception:
        pass

    if not encoding:
        try:
            import chardet

            result = chardet.detect(data)
            encoding = result.get("encoding") if result else None
        except Exception:
            pass

    return data.decode(encoding or "utf-8", errors="replace")


def _extract_pdf(file_path: str) -> str:
    """Extract plain text from a PDF using PyMuPDF."""
    import fitz  # PyMuPDF

    parts: list[str] = []
    with fitz.open(file_path) as doc:
        for page in doc:
            text = page.get_text()
            if text.strip():
                parts.append(text)
    return "\n\n".join(parts)


def _extract_pdf_docling(file_path: str) -> str:
    """Extract structured markdown from a PDF using Docling.

    Produces markdown with section hierarchy, figure captions, tables,
    and equations preserved. Returns empty string on failure so the
    caller can fall back to PyMuPDF plain text.
    """
    try:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(file_path)
        md = result.document.export_to_markdown()
        if md and len(md.strip()) > 100:
            logger.info(
                "Docling produced %d chars of markdown from %s",
                len(md), Path(file_path).name,
            )
            return md
    except ImportError:
        logger.info("Docling not available, skipping markdown extraction")
    except Exception as e:
        logger.warning("Docling extraction failed for %s: %s", file_path, e)
    return ""


def parse_file(file_path: str) -> Document:
    """Parse a single file into a Document."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file format: {suffix}")

    markdown = ""
    if suffix == ".pdf":
        markdown = _extract_pdf_docling(file_path)
        text = _extract_pdf(file_path)
    else:
        text = _read_text_with_fallback(file_path)

    return Document(
        document_id=uuid.uuid4().hex,
        filename=path.name,
        text=text,
        markdown=markdown,
    )


def parse_files(file_paths: list[str]) -> list[Document]:
    """Parse multiple files into Documents."""
    return [parse_file(fp) for fp in file_paths]
