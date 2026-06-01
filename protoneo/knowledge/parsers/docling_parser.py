"""Docling-based PDF parser with layout analysis and figure extraction.

Uses IBM's Docling library for structured document understanding:
layout classification, table extraction, figure bounding boxes, and
markdown output. This is the primary parser for scientific papers.
"""

import logging
from pathlib import Path

from ..types import ParseResult
from ..parser import (
    _build_docling_pipeline_options,
    _docling_version,
    _export_table_payload,
    _resolve_caption,
)

logger = logging.getLogger("protoneo.knowledge.parsers.docling")

_IMAGE_RESOLUTION_SCALE = 2.0


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
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling_core.types.doc import ImageRefMode, PictureItem, TableItem

        options = options or {}
        output_dir = Path(options.get("output_dir", ""))
        if not output_dir:
            output_dir = path.parent / f"{path.stem}_figures"

        pipeline_options = _build_docling_pipeline_options(
            docling_options={
                "images_scale": options.get("images_scale", _IMAGE_RESOLUTION_SCALE),
                "generate_page_images": options.get("generate_page_images", True),
                "generate_picture_images": options.get("generate_picture_images", True),
                "do_ocr": options.get("do_ocr", False),
                "do_formula_enrichment": options.get("do_formula_enrichment", False),
                "do_code_enrichment": options.get("do_code_enrichment", False),
            }
        )

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
        tables: list[dict] = []
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
                caption = _resolve_caption(conv_res.document, element)
                table_payload = _export_table_payload(element, conv_res.document)
                if img:
                    img_filename = f"{path.stem}-table-{table_counter}.png"
                    img_path = output_dir / img_filename
                    img.save(str(img_path), "PNG")
                else:
                    img_path = Path("")

                source_parts = [part for part in (caption, table_payload.get("table_markdown", "")) if part]
                tables.append({
                    "index": table_counter,
                    "page": page_no,
                    "bbox": bbox,
                    "caption": caption,
                    "image_path": str(img_path) if str(img_path) else "",
                    "source_text": "\n\n".join(source_parts),
                    **table_payload,
                })
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
                "docling_version": _docling_version(),
                "figure_count": picture_counter,
                "table_count": table_counter,
                "tables": tables,
            },
        )
