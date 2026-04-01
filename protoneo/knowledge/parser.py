"""
Document parsing entry point.

Uses Docling for layout-aware PDF extraction with figure bounding boxes,
structured tables, and section hierarchy. Plain text and markdown files
are read directly with charset detection.
"""

import logging
import re
import uuid
from pathlib import Path

from ..agents.types import Document

logger = logging.getLogger("protoneo.knowledge.parser")

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".html", ".md", ".markdown", ".txt"}

# Lines that are bare numbers (PDF line number artifacts from two-column layouts)
_BARE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")


def _strip_line_number_pollution(text: str) -> str:
    """Remove leading/trailing runs of bare line numbers from PDF extraction.

    Two-column ACM PDFs often have page line numbers extracted as content.
    This strips consecutive bare-number lines from the start and end of the
    document while preserving legitimate single-number content in the middle.
    """
    lines = text.split("\n")

    start = 0
    while start < len(lines) and _BARE_NUMBER_RE.match(lines[start]):
        start += 1

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
    """Read a text file with charset detection fallback."""
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


def _parse_pdf_docling(file_path: str) -> tuple[str, str, list[dict], str]:
    """Parse a PDF using Docling's layout analysis engine.

    Returns (text, markdown, figures, figures_dir).
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import ImageRefMode, PictureItem, TableItem

    path = Path(file_path)
    output_dir = path.parent / f"{path.stem}_figures"
    output_dir.mkdir(parents=True, exist_ok=True)

    pipeline_options = PdfPipelineOptions()
    pipeline_options.images_scale = 2.0
    pipeline_options.generate_page_images = False
    pipeline_options.generate_picture_images = True

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options,
            ),
        }
    )

    logger.info("Parsing %s with Docling", path.name)
    conv_res = converter.convert(path)

    # Extract figures
    figures: list[dict] = []
    picture_counter = 0
    table_counter = 0

    for element, _level in conv_res.document.iterate_items():
        if isinstance(element, PictureItem):
            picture_counter += 1
            img = element.get_image(conv_res.document)
            if img:
                img_filename = f"{path.stem}-figure-{picture_counter}.png"
                img_path = output_dir / img_filename
                img.save(str(img_path), "PNG")

                bbox = {}
                page_no = 0
                if element.prov:
                    prov = element.prov[0]
                    page_no = prov.page_no
                    if prov.bbox:
                        bbox = {
                            "l": prov.bbox.l, "t": prov.bbox.t,
                            "r": prov.bbox.r, "b": prov.bbox.b,
                        }

                caption = ""
                for cap in getattr(element, "captions", []):
                    if hasattr(cap, "text"):
                        caption = cap.text
                        break

                figures.append({
                    "index": picture_counter,
                    "page": page_no,
                    "bbox": bbox,
                    "caption": caption,
                    "image_path": str(img_path),
                })

        if isinstance(element, TableItem):
            table_counter += 1
            img = element.get_image(conv_res.document)
            if img:
                img_filename = f"{path.stem}-table-{table_counter}.png"
                img_path = output_dir / img_filename
                img.save(str(img_path), "PNG")

    markdown = conv_res.document.export_to_markdown(
        image_mode=ImageRefMode.REFERENCED,
    )
    text = conv_res.document.export_to_markdown(
        image_mode=ImageRefMode.PLACEHOLDER,
    )

    logger.info(
        "Docling extracted %d figures, %d tables, %d chars markdown from %s",
        picture_counter, table_counter, len(markdown), path.name,
    )

    return text, markdown, figures, str(output_dir)


def parse_file(file_path: str, fast: bool = False) -> Document:
    """Parse a single file into a Document.

    For PDFs: uses Docling for layout-aware extraction with figure
    bounding boxes, structured tables, and section hierarchy.

    When fast=True, Docling still runs (it is the only parser) but
    downstream AI enrichment (VLM figure descriptions) is skipped
    by the pipeline.

    For .md/.txt files: reads directly with charset detection.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file format: {suffix}")

    if suffix == ".pdf":
        text, markdown, figures, figures_dir = _parse_pdf_docling(file_path)
        text = _strip_line_number_pollution(text)
        return Document(
            document_id=uuid.uuid4().hex,
            filename=path.name,
            text=text,
            markdown=markdown,
            metadata={
                "figures": figures,
                "figures_dir": figures_dir,
                "parser": "docling",
            },
        )

    if suffix in {".md", ".markdown"}:
        content = _read_text_with_fallback(file_path)
        return Document(
            document_id=uuid.uuid4().hex,
            filename=path.name,
            text=content,
            markdown=content,
        )

    # Plain text
    text = _read_text_with_fallback(file_path)
    return Document(
        document_id=uuid.uuid4().hex,
        filename=path.name,
        text=text,
    )


def parse_files(file_paths: list[str]) -> list[Document]:
    """Parse multiple files into Documents."""
    return [parse_file(fp) for fp in file_paths]
