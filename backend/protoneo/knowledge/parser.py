"""
Document parsing. Reuses the proven extraction logic from the legacy codebase.
"""

import logging
import re
import uuid
from pathlib import Path

from ..agents.types import Document

logger = logging.getLogger("protoneo.knowledge.parser")

SUPPORTED_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt"}

# Lines that are bare numbers (PDF line number artifacts from two-column layouts)
_BARE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")


def _strip_line_number_pollution(text: str) -> str:
    """Remove leading/trailing runs of bare line numbers from PDF extraction.

    Two-column ACM PDFs often have page line numbers extracted as content.
    This strips consecutive bare-number lines from the start and end of the
    document while preserving legitimate single-number content in the middle.
    """
    lines = text.split("\n")

    # Strip leading bare-number lines
    start = 0
    while start < len(lines) and _BARE_NUMBER_RE.match(lines[start]):
        start += 1

    # Strip trailing bare-number lines
    end = len(lines)
    while end > start and _BARE_NUMBER_RE.match(lines[end - 1]):
        end -= 1

    if start > 0 or end < len(lines):
        stripped_leading = start
        stripped_trailing = len(lines) - end
        logger.info(
            "Stripped %d leading and %d trailing line-number lines from PDF text",
            stripped_leading, stripped_trailing,
        )

    return "\n".join(lines[start:end])


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


def _extract_pdf_pdf2md(file_path: str, output_dir: str | None = None) -> tuple[str, str]:
    """Extract structured markdown from a PDF using the pdf2md CLI tool.

    Calls pdf2md as a subprocess with the local AI pipeline (Nemotron for
    text reasoning, Qwen3-VL for figure descriptions). Falls back to plain
    PyMuPDF extraction on failure.

    Returns (markdown, figures_dir_path). The markdown includes proper section
    headers, linked citations, reflowed paragraphs, and VLM figure descriptions.
    """
    import os
    import subprocess

    pdf_path = Path(file_path)
    if output_dir:
        out_dir = Path(output_dir)
    else:
        # Store pdf2md output alongside session data so figures persist
        sessions_dir = Path(__file__).resolve().parents[2] / "data" / "sessions" / "pdf2md"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        out_dir = sessions_dir

    # Build environment for pdf2md's local AI endpoints.
    # Pull from ProtoNeo settings (LAN endpoints) when available,
    # fall back to hardcoded defaults for the homelab.
    env = os.environ.copy()
    try:
        from ..llm.settings import load_settings, endpoint_map
        settings = load_settings()
        endpoints = endpoint_map(settings)
        dynamo = endpoints.get("lan-dynamo")
        if dynamo:
            env.setdefault("LM_STUDIO_HOST", dynamo.url)
    except Exception:
        pass
    env.setdefault("LM_STUDIO_HOST", "http://192.168.86.143:1234/v1")
    env.setdefault("PDF2MD_TEXT_MODEL", "nemotron-cascade-2-30b-a3b-i1")
    env.setdefault("PDF2MD_VLM_HOST", "http://192.168.86.141:8080/v1")
    env.setdefault("PDF2MD_VLM_MODEL", "Qwen3-VL-30B-A3B-Thinking-Q5_K_XL")

    # Find pdf2md executable
    pdf2md_repo = Path.home() / "tools" / "paper-to-md"
    cmd = [
        str(pdf2md_repo / ".venv" / "bin" / "pdf2md"),
        "convert",
        str(pdf_path),
        str(out_dir),
        "--depth", "high",
        "--local",
        "--keep-raw",
    ]

    logger.info("Running pdf2md: %s", " ".join(cmd[:6]))

    try:
        result = subprocess.run(
            cmd,
            env=env,
            cwd=str(pdf2md_repo),
            capture_output=True,
            text=True,
            timeout=600,
        )

        if result.returncode != 0:
            logger.warning("pdf2md failed (exit %d): %s", result.returncode, result.stderr[:500])
            return "", ""

        # Find the output markdown
        stem = pdf_path.stem
        md_path = out_dir / stem / f"{stem}.md"
        if md_path.exists():
            md = md_path.read_text(encoding="utf-8")
            figures_dir = str(out_dir / stem / "img")
            logger.info(
                "pdf2md produced %d chars of markdown from %s",
                len(md), pdf_path.name,
            )
            return md, figures_dir

        logger.warning("pdf2md output not found at %s", md_path)
        return "", ""

    except subprocess.TimeoutExpired:
        logger.warning("pdf2md timed out after 600s for %s", pdf_path.name)
        return "", ""
    except FileNotFoundError:
        logger.info("pdf2md not installed at %s", pdf2md_repo)
        return "", ""
    except Exception as e:
        logger.warning("pdf2md failed for %s: %s", pdf_path.name, e)
        return "", ""


def parse_file(file_path: str, fast: bool = False) -> Document:
    """Parse a single file into a Document.

    When fast=True, skip AI extraction and use PyMuPDF only (~2-5 seconds).
    When fast=False (default), try pdf2md first for structured markdown with
    AI-powered cleanup, figure descriptions, and section detection.
    Falls back to plain PyMuPDF text if pdf2md is unavailable.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file format: {suffix}")

    markdown = ""
    if suffix == ".pdf":
        # Check for pre-built markdown from pdf2md batch runs.
        # The PDF may be uploaded to a temp dir, so check both:
        #   1. Sibling: same directory as PDF (reviews-pending/paperN/paperN.md)
        #   2. Known location: reviews-pending/paperN/paperN.md by extracting paper ID from filename
        stem = path.stem  # e.g., "uuid_hpdc26-paper251" or "hpdc26-paper251"
        # Extract paper ID: find "paperNNN" anywhere in the filename
        import re as _re
        paper_match = _re.search(r"(paper\d+)", stem)
        paper_id = paper_match.group(1) if paper_match else ""

        prebuilt_md = None
        if paper_id:
            # Check sibling first
            sibling_md = path.parent / f"{paper_id}.md"
            if sibling_md.exists() and sibling_md.stat().st_size > 1000:
                prebuilt_md = sibling_md
            else:
                # Check reviews-pending/ directory
                reviews_dir = Path(__file__).resolve().parents[3] / "reviews-pending" / paper_id
                candidate = reviews_dir / f"{paper_id}.md"
                if candidate.exists() and candidate.stat().st_size > 1000:
                    prebuilt_md = candidate

        if prebuilt_md:
            markdown = _read_text_with_fallback(str(prebuilt_md))
            logger.info("Using pre-built markdown: %s (%d chars)", prebuilt_md, len(markdown))
        elif not fast:
            markdown, _figures_dir = _extract_pdf_pdf2md(file_path)
        text = _extract_pdf(file_path)
        text = _strip_line_number_pollution(text)
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
