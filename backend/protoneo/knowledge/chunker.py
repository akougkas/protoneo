"""
Text chunking with sentence-boundary awareness.
"""

from ..agents.types import Document


def chunk_text(text: str, chunk_size: int = 2000, overlap: int = 200) -> list[str]:
    """
    Split text into chunks, preferring sentence boundaries.

    Default chunk size is larger than the legacy 500-char setting because
    the kernel sends chunks to LLMs with large context windows.
    """
    if len(text) <= chunk_size:
        return [text] if text.strip() else []

    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        if end < len(text):
            # Find nearest sentence boundary
            for sep in [".\n", "!\n", "?\n", "\n\n", ". ", "! ", "? "]:
                last_sep = text[start:end].rfind(sep)
                if last_sep != -1 and last_sep > chunk_size * 0.3:
                    end = start + last_sep + len(sep)
                    break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        start = end - overlap if end < len(text) else len(text)

    return chunks


def chunk_document(doc: Document, chunk_size: int = 2000, overlap: int = 200) -> Document:
    """Chunk a Document's text and store the chunks on the document."""
    doc.chunks = chunk_text(doc.text, chunk_size, overlap)
    return doc
