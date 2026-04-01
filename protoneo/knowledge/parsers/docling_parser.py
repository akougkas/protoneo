"""Docling-based PDF parser with layout analysis and figure extraction.

Uses IBM's Docling library for structured document understanding:
layout classification, table extraction, figure bounding boxes, and
markdown output. This is the primary parser for scientific papers.
"""

import logging
from pathlib import Path

from ..types import ParseResult

logger = logging.getLogger("protoneo.knowledge.parsers.docling")

_IMAGE_RESOLUTION_SCALE = 2.0


def _resolve_caption(document, element) -> str:
    """Resolve caption text from a PictureItem's RefItem references."""
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


class DoclingParser:
    """Extracts structured content from PDFs using Docling's layout analysis.

    Produces rich markdown with section hierarchy, structured tables,
    and figure placeholders. Extracted figure images are saved to disk
    for downstream VLM enrichment.
    """

    @property
    def name(self) -> str:
        return "docling"

    @property
    def supported_extensions(self) -> set[str]:
        return {".pdf", ".docx", ".pptx", ".html"}

    def available(self) -> bool:
        try:
            from docling.document_converter import DocumentConverter  # noqa: F401
            return True
        except Exception:
            return False

    async def parse(self, path: Path, options: dict | None = None) -> ParseResult:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_core.types.doc import ImageRefMode, PictureItem, TableItem

        options = options or {}
        output_dir = Path(options.get("output_dir", ""))
        if not output_dir:
            output_dir = path.parent / f"{path.stem}_figures"

        pipeline_options = PdfPipelineOptions()
        pipeline_options.images_scale = _IMAGE_RESOLUTION_SCALE
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

        # Extract figures and tables
        output_dir.mkdir(parents=True, exist_ok=True)
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

                    # Extract bounding box from the element's provenance
                    bbox = {}
                    page_no = 0
                    if element.prov:
                        prov = element.prov[0]
                        page_no = prov.page_no
                        if prov.bbox:
                            bbox = {
                                "l": prov.bbox.l,
                                "t": prov.bbox.t,
                                "r": prov.bbox.r,
                                "b": prov.bbox.b,
                            }

                    # Resolve caption from document reference
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

        # Export markdown with image references
        markdown = conv_res.document.export_to_markdown(
            image_mode=ImageRefMode.REFERENCED,
        )

        # Build plain text (strip markdown formatting for downstream text consumers)
        text = conv_res.document.export_to_markdown(
            image_mode=ImageRefMode.PLACEHOLDER,
        )

        logger.info(
            "Docling extracted %d figures, %d tables, %d chars markdown from %s",
            picture_counter, table_counter, len(markdown), path.name,
        )

        return ParseResult(
            text=text,
            markdown=markdown,
            figures_dir=str(output_dir),
            figures=figures,
            metadata={
                "parser": "docling",
                "figure_count": picture_counter,
                "table_count": table_counter,
            },
        )
