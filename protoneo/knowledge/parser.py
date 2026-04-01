"""
Document parsing entry point.

Uses Docling for layout-aware PDF extraction with integrated VLM
figure descriptions. When a VLM endpoint is configured, Docling
describes every figure inline during parsing in a single pass.
"""

import logging
import re
import uuid
from pathlib import Path
from typing import Any

from ..agents.types import Document

logger = logging.getLogger("protoneo.knowledge.parser")

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".html", ".md", ".markdown", ".txt"}

# Lines that are bare numbers (PDF line number artifacts from two-column layouts)
_BARE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")


def _resolve_caption(document, element) -> str:
    """Resolve caption text from a Docling PictureItem's RefItem references."""
    captions = getattr(element, "captions", [])
    if not captions:
        return ""
    for cap_ref in captions:
        cref = getattr(cap_ref, "cref", "")
        if not cref:
            continue
        parts = cref.lstrip("#/").split("/")
        if len(parts) == 2:
            collection_name, idx_str = parts
            try:
                idx = int(idx_str)
            except ValueError:
                continue
            collection = getattr(document, collection_name, [])
            if idx < len(collection):
                item = collection[idx]
                text = getattr(item, "text", "")
                if text:
                    return text
    return ""


def _strip_line_number_pollution(text: str) -> str:
    """Remove bare line numbers from two-column PDF extraction.

    ACM and IEEE two-column papers have page-margin line numbers that get
    extracted as standalone lines. This removes all bare-number lines that
    appear to be sequential page line numbers.
    """
    lines = text.split("\n")
    result: list[str] = []
    stripped = 0
    last_num = -100

    for line in lines:
        if _BARE_NUMBER_RE.match(line):
            try:
                num = int(line.strip())
            except ValueError:
                result.append(line)
                continue
            if abs(num - last_num) <= 3 or (last_num < 0 and num <= 5):
                last_num = num
                stripped += 1
                continue
            last_num = num
            stripped += 1
            continue
        else:
            last_num = -100
        result.append(line)

    if stripped > 0:
        logger.info("Stripped %d bare line-number lines from PDF text", stripped)

    return "\n".join(result)


def _clean_markdown(md: str) -> str:
    """Final cleansing pass on Docling markdown output."""
    md = re.sub(r"\n*<!-- image -->\n*", "\n\n", md)

    # Demote headings injected by VLM descriptions into bold text.
    # VLM responses use ### and #### for structure (e.g. "### Chart Type
    # and Axes") which pollutes the paper's actual section hierarchy.
    md = re.sub(r"^#{3,6}\s+\**(.+?)\**\s*$", r"**\1**", md, flags=re.MULTILINE)

    # Remove headings that are just bare numbers (line number artifacts).
    md = re.sub(r"^#{1,6}\s+\d{1,5}\s*$", "", md, flags=re.MULTILINE)

    # Strip table garbage after the References section.
    # Docling sometimes extracts two-column bibliographies as a markdown
    # table with line numbers in the first column. These appear after the
    # clean list-style references and add no value.
    ref_match = re.search(r"^## References\s*$", md, re.MULTILINE)
    if ref_match:
        before_refs = md[:ref_match.start()]
        refs_section = md[ref_match.start():]
        # Keep list items (- [1] ...) and headings, strip table rows
        ref_lines = refs_section.split("\n")
        cleaned_ref_lines = [
            l for l in ref_lines
            if not l.startswith("|") and not re.match(r"^\|[-\s|]+\|$", l)
        ]
        md = before_refs + "\n".join(cleaned_ref_lines)

    # Remove orphaned link fragments (e.g. "[. 1-15. doi:...](...)")
    md = re.sub(r"^\[[\.\s,\d–-]+doi:[^\]]*\]\([^)]*\)\s*$", "", md, flags=re.MULTILINE)

    md = re.sub(r"\n{3,}", "\n\n", md)
    md = "\n".join(line.rstrip() for line in md.split("\n"))
    return md.strip()


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


def _build_docling_pipeline_options(vlm_config: dict[str, Any] | None = None):
    """Build Docling PdfPipelineOptions with optional VLM integration.

    Args:
        vlm_config: Optional dict with keys:
            - url: VLM API endpoint (e.g., "http://host:8081/v1/chat/completions")
            - model: model name (e.g., "qwen3-vl-8b")
            - prompt: description prompt
            - temperature, top_p: inference params
            - timeout: request timeout in seconds
    """
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    opts = PdfPipelineOptions()
    opts.images_scale = 2.0
    opts.generate_page_images = False
    opts.generate_picture_images = True
    opts.do_ocr = False

    if vlm_config and vlm_config.get("url"):
        from docling.datamodel.pipeline_options import PictureDescriptionApiOptions

        params: dict[str, Any] = {}
        if vlm_config.get("model"):
            params["model"] = vlm_config["model"]
        if vlm_config.get("temperature") is not None:
            params["temperature"] = vlm_config["temperature"]
        if vlm_config.get("top_p") is not None:
            params["top_p"] = vlm_config["top_p"]

        opts.do_picture_description = True
        opts.enable_remote_services = True
        opts.picture_description_options = PictureDescriptionApiOptions(
            url=vlm_config["url"],
            params=params,
            timeout=vlm_config.get("timeout", 300.0),
            concurrency=vlm_config.get("concurrency", 1),
            prompt=vlm_config.get("prompt", (
                "You are an expert scientific figure analyst. "
                "Describe this figure in 500 words for a peer reviewer who cannot see it. "
                "Cover: chart type, axes, data series with colors, trends, and key observations. "
                "Write as continuous prose paragraphs. Do not use markdown headings."
            )),
        )
        logger.info("Docling VLM enabled: %s (model=%s)", vlm_config["url"], vlm_config.get("model", "default"))

    return opts


def _parse_pdf_docling(
    file_path: str,
    vlm_config: dict[str, Any] | None = None,
) -> tuple[str, str, list[dict], str]:
    """Parse a PDF using Docling's layout analysis engine.

    When vlm_config is provided, Docling describes every figure inline
    during parsing using the configured VLM endpoint. No separate
    enrichment step needed.

    Returns (text, markdown, figures, figures_dir).
    """
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import ImageRefMode, PictureItem, TableItem

    path = Path(file_path)
    output_dir = path.parent / f"{path.stem}_figures"
    output_dir.mkdir(parents=True, exist_ok=True)

    pipeline_options = _build_docling_pipeline_options(vlm_config)

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

                caption = _resolve_caption(conv_res.document, element)

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


def parse_file(
    file_path: str,
    fast: bool = False,
    vlm_config: dict[str, Any] | None = None,
) -> Document:
    """Parse a single file into a Document.

    For PDFs: uses Docling for layout-aware extraction with figure
    bounding boxes, structured tables, and section hierarchy.

    When vlm_config is provided and fast=False, Docling uses the VLM
    to describe every figure inline during parsing. No separate
    enrichment step is needed.

    Args:
        file_path: Path to the file.
        fast: If True, skip VLM enrichment (structure extraction only).
        vlm_config: Optional VLM endpoint configuration dict with keys:
            url, model, prompt, temperature, top_p, timeout.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file format: {suffix}")

    if suffix == ".pdf":
        effective_vlm = None if fast else vlm_config
        text, markdown, figures, figures_dir = _parse_pdf_docling(file_path, effective_vlm)
        text = _strip_line_number_pollution(text)
        markdown = _strip_line_number_pollution(markdown)
        text = _clean_markdown(text)
        markdown = _clean_markdown(markdown)
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
