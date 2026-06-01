"""
Document parsing entry point.

Uses Docling for layout-aware PDF extraction with optional integrated
VLM figure descriptions. When a VLM endpoint is explicitly enabled,
Docling describes every figure inline during parsing in a single pass.
"""

import logging
import re
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from ..agents.types import Document
from .visual_evidence import describe_image, extract_numeric_claims, sanitize_description

logger = logging.getLogger("protoneo.knowledge.parser")

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".html", ".md", ".markdown", ".txt"}

# Lines that are bare numbers (PDF line number artifacts from two-column layouts)
_BARE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")
_FORMULA_NOT_DECODED_RE = re.compile(r"<!--\s*formula-not-decoded\s*-->", re.IGNORECASE)
_EQUATION_PLACEHOLDER_RE = re.compile(
    r"\[Equation\s+(?P<index>\d+)\s+not decoded;\s+see graph evidence\]",
    re.IGNORECASE,
)


def _docling_version() -> str:
    try:
        return version("docling")
    except PackageNotFoundError:
        return "unknown"


def _resolve_caption(document, element) -> str:
    """Resolve caption text from a Docling PictureItem's RefItem references."""
    caption_text = getattr(element, "caption_text", None)
    if callable(caption_text):
        try:
            text = caption_text(document)
            if text:
                return str(text)
        except Exception:
            pass

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


def _nonempty_context(lines: list[str], line_index: int, direction: int) -> str:
    i = line_index + direction
    while 0 <= i < len(lines):
        value = lines[i].strip()
        if value and not _FORMULA_NOT_DECODED_RE.fullmatch(value):
            return value
        i += direction
    return ""


def _record_for_formula_placeholder(
    *,
    index: int,
    line_number: int,
    before: str,
    after: str,
    decoded_text: str = "",
    page: int = 0,
    bbox: dict[str, Any] | None = None,
    image_path: str = "",
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decoded = bool(decoded_text.strip())
    source_text = decoded_text.strip() if decoded else f"Equation {index} not decoded"
    warning = "" if decoded else "Docling emitted formula-not-decoded and did not expose recoverable formula text."
    return {
        "index": index,
        "kind": "equation",
        "page": page,
        "bbox": bbox or {},
        "image_path": image_path,
        "source_text": source_text,
        "surrounding_context": {
            "before": before,
            "after": after,
        },
        "description": source_text if decoded else "",
        "description_source": "docling" if decoded else "none",
        "confidence": 0.6 if decoded else 0.0,
        "grounding": "formula_decoded" if decoded else "formula_not_decoded",
        "warning": warning,
        "provenance": {
            "parser": "docling",
            "source": "formula_placeholder",
            "line_number": line_number,
            **(provenance or {}),
        },
    }


def _recover_formula_text_from_docling_record(record: dict[str, Any] | None) -> str:
    """Return Docling-exposed formula text/LaTeX when it is not just the placeholder."""
    if not isinstance(record, dict):
        return ""
    for key in ("latex", "text", "source_text", "orig", "content"):
        value = str(record.get(key) or "").strip()
        if value and not _FORMULA_NOT_DECODED_RE.search(value):
            return value
    return ""


def repair_formula_placeholders(
    markdown: str,
    *,
    docling_formulas: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Replace invisible Docling formula comments and return Equation evidence records.

    The repair is idempotent for already-readable placeholders. Existing
    placeholders are converted back into Equation records so graph refreshes can
    rebuild the evidence without rerunning PDF parsing.
    """
    if not markdown:
        return markdown, []

    formulas = docling_formulas or []
    records: list[dict[str, Any]] = []
    lines = markdown.splitlines()
    next_index = 1
    repaired_lines: list[str] = []

    for line_number, line in enumerate(lines, 1):
        existing = _EQUATION_PLACEHOLDER_RE.search(line)
        if existing and not _FORMULA_NOT_DECODED_RE.search(line):
            index = int(existing.group("index"))
            next_index = max(next_index, index + 1)
            records.append(_record_for_formula_placeholder(
                index=index,
                line_number=line_number,
                before=_nonempty_context(lines, line_number - 1, -1),
                after=_nonempty_context(lines, line_number - 1, 1),
            ))
            repaired_lines.append(line)
            continue

        if not _FORMULA_NOT_DECODED_RE.search(line):
            repaired_lines.append(line)
            continue

        record_hint = formulas[next_index - 1] if next_index - 1 < len(formulas) else {}
        recovered = _recover_formula_text_from_docling_record(record_hint)
        replacement = recovered if recovered else f"[Equation {next_index} not decoded; see graph evidence]"
        repaired_lines.append(_FORMULA_NOT_DECODED_RE.sub(replacement, line))
        records.append(_record_for_formula_placeholder(
            index=next_index,
            line_number=line_number,
            before=_nonempty_context(lines, line_number - 1, -1),
            after=_nonempty_context(lines, line_number - 1, 1),
            decoded_text=recovered,
            page=int(record_hint.get("page", 0) or 0) if isinstance(record_hint, dict) else 0,
            bbox=dict(record_hint.get("bbox", {}) or {}) if isinstance(record_hint, dict) else {},
            image_path=str(record_hint.get("image_path", "") or "") if isinstance(record_hint, dict) else "",
            provenance={"docling_item": record_hint.get("item_type", "")} if isinstance(record_hint, dict) else {},
        ))
        next_index += 1

    return "\n".join(repaired_lines), records


def _equation_records_from_docling_formulas(
    formulas: list[dict[str, Any]] | None,
    existing: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Merge Docling FormulaItem records into Equation evidence records."""
    by_index: dict[int, dict[str, Any]] = {}
    order: list[int] = []
    for item in existing or []:
        try:
            index = int(item.get("index", len(order) + 1))
        except (TypeError, ValueError):
            index = len(order) + 1
        by_index[index] = dict(item)
        order.append(index)

    for i, formula in enumerate(formulas or [], 1):
        if not isinstance(formula, dict):
            continue
        try:
            index = int(formula.get("index", i) or i)
        except (TypeError, ValueError):
            index = i
        recovered = _recover_formula_text_from_docling_record(formula)
        record = _record_for_formula_placeholder(
            index=index,
            line_number=0,
            before="",
            after="",
            decoded_text=recovered,
            page=int(formula.get("page", 0) or 0),
            bbox=dict(formula.get("bbox", {}) or {}),
            image_path=str(formula.get("image_path", "") or ""),
            provenance={"docling_item": formula.get("item_type", "")},
        )
        if index not in by_index:
            by_index[index] = record
            order.append(index)
            continue
        existing_record = by_index[index]
        if recovered and existing_record.get("grounding") != "formula_decoded":
            existing_record.update({
                "source_text": record["source_text"],
                "description": record["description"],
                "description_source": record["description_source"],
                "confidence": record["confidence"],
                "grounding": record["grounding"],
                "warning": "",
            })
        for key in ("page", "bbox", "image_path"):
            if record.get(key) not in (None, "", {}, 0):
                existing_record[key] = record[key]
        provenance = dict(existing_record.get("provenance", {}) or {})
        provenance.update(record.get("provenance", {}) or {})
        existing_record["provenance"] = provenance

    return [by_index[index] for index in order]


def _collect_picture_annotation(element: Any) -> str:
    """Return Docling inline VLM description text for a PictureItem, if present."""
    for annotation in getattr(element, "annotations", []) or []:
        text = getattr(annotation, "text", "")
        if text:
            return sanitize_description(text)
    return ""


def _export_table_payload(element: Any, document: Any) -> dict[str, str]:
    """Export Docling table structure into durable text artifacts."""
    payload = {"table_markdown": "", "table_html": "", "table_otsl": ""}
    exporters = {
        "table_markdown": "export_to_markdown",
        "table_html": "export_to_html",
        "table_otsl": "export_to_otsl",
    }
    for key, method_name in exporters.items():
        method = getattr(element, method_name, None)
        if not callable(method):
            continue
        try:
            value = method(document)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Docling table %s failed: %s", method_name, exc)
            continue
        if value:
            payload[key] = str(value).strip()
    return payload


def _build_artifact_records(
    picture_items: list[tuple[str, int, dict[str, Any], str, Any]],
    table_items: list[tuple[str, int, dict[str, Any], str, dict[str, str]]],
    vlm_config: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build provenance-bearing figure/table artifact records."""
    figures: list[dict[str, Any]] = []
    for idx, (image_path, page, bbox, caption, element) in enumerate(picture_items, 1):
        description = _collect_picture_annotation(element) if vlm_config else ""
        source_text = caption or f"Extracted figure image from page {page}"
        figures.append({
            "index": idx,
            "kind": "figure",
            "page": page,
            "bbox": bbox,
            "caption": caption,
            "source_text": source_text,
            "image_path": image_path,
            "description": description,
            "description_source": "vlm" if description else "none",
            "numeric_claims": extract_numeric_claims(description) if description else [],
            "confidence": 0.6 if description else 0.0,
            "model": (vlm_config or {}).get("model", ""),
            "endpoint": (vlm_config or {}).get("url", ""),
            "grounding": "visual" if description else "extracted_no_vlm",
            "provenance": {
                "parser": "docling",
                "page": page,
                "bbox": bbox,
                "image_path": image_path,
            },
        })

    tables: list[dict[str, Any]] = []
    for idx, (image_path, page, bbox, caption, table_payload) in enumerate(table_items, 1):
        if vlm_config and image_path:
            record = describe_image(image_path, vlm_config, kind="table", caption=caption)
        else:
            record = {
                "kind": "table",
                "image_path": image_path,
                "caption": caption,
                "description": "",
                "description_source": "none",
                "numeric_claims": [],
                "confidence": 0.0,
                "model": "",
                "endpoint": "",
                "grounding": "extracted_no_vlm",
                "error": "",
            }
        table_markdown = (table_payload or {}).get("table_markdown", "").strip()
        source_parts = [part for part in (caption, table_markdown) if part]
        source_text = "\n\n".join(source_parts) or f"Extracted table evidence from page {page}"
        record.update({
            "index": idx,
            "page": page,
            "bbox": bbox,
            "source_text": source_text,
            "table_markdown": table_markdown,
            "table_html": (table_payload or {}).get("table_html", ""),
            "table_otsl": (table_payload or {}).get("table_otsl", ""),
            "provenance": {
                "parser": "docling",
                "page": page,
                "bbox": bbox,
                "image_path": image_path,
            },
        })
        tables.append(record)
    return figures, tables


def _extract_docling_formula_records(
    document: Any,
    output_dir: Path | None = None,
    stem: str = "document",
) -> list[dict[str, Any]]:
    """Best-effort extraction of Docling formula item text and provenance."""
    records: list[dict[str, Any]] = []
    for element, _level in document.iterate_items():
        item_type = type(element).__name__
        values: dict[str, Any] = {"item_type": item_type}
        for key in ("text", "latex", "orig", "content"):
            value = getattr(element, key, "")
            if value:
                values[key] = str(value)
        if "Formula" not in item_type and not _FORMULA_NOT_DECODED_RE.search(
            " ".join(str(v) for v in values.values())
        ):
            continue

        bbox = {}
        page_no = 0
        if getattr(element, "prov", None):
            prov = element.prov[0]
            page_no = getattr(prov, "page_no", 0) or 0
            if getattr(prov, "bbox", None):
                bbox = {
                    "l": prov.bbox.l,
                    "t": prov.bbox.t,
                    "r": prov.bbox.r,
                    "b": prov.bbox.b,
                }
        records.append({
            **values,
            "index": len(records) + 1,
            "page": page_no,
            "bbox": bbox,
        })
        get_image = getattr(element, "get_image", None)
        if callable(get_image) and output_dir is not None:
            try:
                img = get_image(document)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Docling formula image extraction failed: %s", exc)
                img = None
            if img:
                image_path = output_dir / f"{stem}-equation-{len(records)}.png"
                img.save(str(image_path), "PNG")
                records[-1]["image_path"] = str(image_path)
    return records


_TABLE_CAPTION_RE = re.compile(
    r"^\s*(?:TABLE|Table|Tab\.?)\s+([IVXLCDM]+|\d+)\b[:.\s-]*(.*)$"
)
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


def _roman_to_int(value: str) -> int:
    numerals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for char in reversed(value.upper()):
        current = numerals.get(char, 0)
        if current < prev:
            total -= current
        else:
            total += current
            prev = current
    return total


def _table_ordinal(caption: str) -> int:
    match = _TABLE_CAPTION_RE.search(caption or "")
    if not match:
        return 0
    raw = match.group(1)
    if raw.isdigit():
        return int(raw)
    return _roman_to_int(raw)


def _normalize_table_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _table_dedupe_keys(item: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    caption = str(item.get("caption") or "").strip()
    if caption and not caption.lower().startswith("markdown table"):
        keys.add("caption:" + _normalize_table_key(caption))
    for field in ("table_markdown", "source_text"):
        value = str(item.get(field) or "").strip()
        if value:
            keys.add("content:" + _normalize_table_key(value)[:1000])
    return {key for key in keys if len(key) > 16}


def _table_word_set(item: dict[str, Any]) -> set[str]:
    text = str(item.get("table_markdown") or item.get("source_text") or "")
    words = set(re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text.lower()))
    stop = {"table", "caption", "source", "description", "markdown"}
    return words - stop


def _is_duplicate_table(candidate: dict[str, Any], existing_item: dict[str, Any]) -> bool:
    candidate_words = _table_word_set(candidate)
    existing_words = _table_word_set(existing_item)
    if len(candidate_words) < 8 or len(existing_words) < 8:
        return False
    overlap = len(candidate_words & existing_words) / min(len(candidate_words), len(existing_words))
    return overlap >= 0.65


def extract_markdown_table_records(
    markdown: str,
    *,
    start_index: int = 1,
    existing: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Extract markdown table blocks as first-class text table evidence."""
    seen: set[str] = set()
    for item in existing or []:
        if isinstance(item, dict):
            seen.update(_table_dedupe_keys(item))
    records: list[dict[str, Any]] = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.lstrip().startswith("|"):
            i += 1
            continue

        block_start = i
        block: list[str] = []
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            block.append(lines[i].rstrip())
            i += 1
        if len(block) < 2 or not any(_TABLE_SEPARATOR_RE.match(row) for row in block[:3]):
            continue

        caption = ""
        for j in range(block_start - 1, max(-1, block_start - 8), -1):
            prev = lines[j].strip()
            if not prev:
                continue
            if _TABLE_CAPTION_RE.search(prev) or " table " in f" {prev.lower()} ":
                caption = prev
                break
            if prev.startswith("|"):
                break

        source_text = (caption + "\n" if caption else "") + "\n".join(block)
        candidate = {
            "caption": caption,
            "source_text": source_text,
            "table_markdown": "\n".join(block),
        }
        keys = _table_dedupe_keys(candidate)
        if keys and seen.intersection(keys):
            continue
        if existing and any(
            isinstance(item, dict) and _is_duplicate_table(candidate, item)
            for item in existing
        ):
            seen.update(keys)
            continue
        ordinal = _table_ordinal(caption)
        if existing and 1 <= ordinal <= len(existing):
            target = existing[ordinal - 1]
            if isinstance(target, dict):
                target["table_markdown"] = "\n".join(block)
                target["source_text"] = source_text
                if caption and not target.get("caption"):
                    target["caption"] = caption
                target.setdefault("provenance", {})["source"] = "docling_table_plus_markdown_cells"
                seen.update(keys)
                continue
        seen.update(keys)
        index = start_index + len(records)
        records.append({
            "index": index,
            "kind": "table",
            "page": 0,
            "bbox": {},
            "caption": caption or f"Markdown table {index}",
            "source_text": source_text,
            "image_path": "",
            "description": "",
            "description_source": "markdown",
            "numeric_claims": extract_numeric_claims(source_text),
            "confidence": 0.0,
            "model": "",
            "endpoint": "",
            "grounding": "text_table",
            "provenance": {
                "parser": "docling_markdown",
                "source": "markdown_table",
                "row_count": len(block),
            },
        })
    return records


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


def _build_docling_pipeline_options(
    vlm_config: dict[str, Any] | None = None,
    docling_options: dict[str, Any] | None = None,
):
    """Build Docling PdfPipelineOptions with optional VLM integration.

    Args:
        vlm_config: Optional dict with keys:
            - enabled: whether VLM figure descriptions should run
            - url: VLM API endpoint (e.g., "http://host:8081/v1/chat/completions")
            - model: model name (e.g., "qwen3-vl-8b")
            - prompt: description prompt
            - temperature, top_p: inference params
            - timeout: request timeout in seconds
    """
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TableFormerMode,
        TableStructureOptions,
    )

    docling_options = docling_options or {}
    opts = PdfPipelineOptions()
    opts.images_scale = float(docling_options.get("images_scale", 2.0))
    # Docling 2.96 uses page images for reliable FloatingItem crops; this
    # materially improves table image survival compared with the old setup.
    opts.generate_page_images = bool(docling_options.get("generate_page_images", True))
    opts.generate_picture_images = bool(docling_options.get("generate_picture_images", True))
    opts.do_ocr = bool(docling_options.get("do_ocr", False))
    opts.do_table_structure = bool(docling_options.get("do_table_structure", True))
    opts.table_structure_options = TableStructureOptions(
        do_cell_matching=bool(docling_options.get("do_cell_matching", True)),
        mode=TableFormerMode(str(docling_options.get("table_mode", "accurate")).lower()),
    )
    opts.do_formula_enrichment = bool(docling_options.get("do_formula_enrichment", False))
    opts.do_code_enrichment = bool(docling_options.get("do_code_enrichment", False))
    opts.do_picture_classification = bool(docling_options.get("do_picture_classification", False))
    opts.do_chart_extraction = bool(docling_options.get("do_chart_extraction", False))

    if vlm_config and vlm_config.get("url") and vlm_config.get("enabled", True):
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
            timeout=vlm_config.get("timeout", 120.0),
            concurrency=vlm_config.get("concurrency", 1),
            provenance=vlm_config.get("model", ""),
            prompt=vlm_config.get("prompt", (
                "Describe the figure concisely for paper review. "
                "Identify the chart or diagram type, axes or components, main trends, "
                "and any important quantitative observations. Use short prose only; "
                "do not include markdown headings."
            )),
        )
        logger.info("Docling VLM enabled: %s (model=%s)", vlm_config["url"], vlm_config.get("model", "default"))

    return opts


def _parse_pdf_docling(
    file_path: str,
    vlm_config: dict[str, Any] | None = None,
    docling_options: dict[str, Any] | None = None,
) -> tuple[str, str, list[dict], list[dict], list[dict], str, int]:
    """Parse a PDF using Docling's layout analysis engine.

    When vlm_config is provided, Docling describes every figure inline
    during parsing using the configured VLM endpoint. No separate
    enrichment step needed.

    Returns (text, markdown, figures, tables, figures_dir, table_count).
    """
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import ImageRefMode, PictureItem, TableItem

    path = Path(file_path)
    output_dir = path.parent / f"{path.stem}_figures"
    output_dir.mkdir(parents=True, exist_ok=True)

    pipeline_options = _build_docling_pipeline_options(vlm_config, docling_options)

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options,
            ),
        }
    )

    logger.info("Parsing %s with Docling", path.name)
    conv_res = converter.convert(path)

    # Extract figures/tables.
    picture_items: list[tuple[str, int, dict[str, Any], str, Any]] = []
    table_items: list[tuple[str, int, dict[str, Any], str, dict[str, str]]] = []
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

                picture_items.append((str(img_path), page_no, bbox, caption, element))

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
                        "l": prov.bbox.l, "t": prov.bbox.t,
                        "r": prov.bbox.r, "b": prov.bbox.b,
                    }
            img_path = Path("")
            if img:
                img_filename = f"{path.stem}-table-{table_counter}.png"
                img_path = output_dir / img_filename
                img.save(str(img_path), "PNG")

            caption = _resolve_caption(conv_res.document, element)
            table_payload = _export_table_payload(element, conv_res.document)
            table_items.append((str(img_path) if str(img_path) else "", page_no, bbox, caption, table_payload))

    figures, tables = _build_artifact_records(picture_items, table_items, vlm_config)
    formulas = _extract_docling_formula_records(conv_res.document, output_dir, path.stem)

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

    return text, markdown, figures, tables, formulas, str(output_dir), table_counter


def parse_file(
    file_path: str,
    fast: bool = False,
    vlm_config: dict[str, Any] | None = None,
    docling_options: dict[str, Any] | None = None,
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
        text, markdown, figures, tables, formulas, figures_dir, table_count = _parse_pdf_docling(
            file_path,
            effective_vlm,
            docling_options,
        )
        text = _strip_line_number_pollution(text)
        markdown = _strip_line_number_pollution(markdown)
        text = _clean_markdown(text)
        markdown = _clean_markdown(markdown)
        text, text_equations = repair_formula_placeholders(text, docling_formulas=formulas)
        markdown, equations = repair_formula_placeholders(markdown, docling_formulas=formulas)
        if not equations and text_equations:
            equations = text_equations
        equations = _equation_records_from_docling_formulas(formulas, equations)
        markdown_tables = extract_markdown_table_records(
            markdown,
            start_index=len(tables) + 1,
            existing=tables,
        )
        if markdown_tables:
            tables = [*tables, *markdown_tables]
            table_count = max(table_count, len(tables))
        return Document(
            document_id=uuid.uuid4().hex,
            filename=path.name,
            text=text,
            markdown=markdown,
            metadata={
                "figures": figures,
                "tables": tables,
                "equations": equations,
                "table_count": table_count,
                "equation_count": len(equations),
                "figures_dir": figures_dir,
                "parser": "docling",
                "docling_version": _docling_version(),
                "docling_options": docling_options or {},
                "vlm_used": bool(effective_vlm),
            },
        )

    if suffix in {".md", ".markdown"}:
        content = _read_text_with_fallback(file_path)
        content, equations = repair_formula_placeholders(content)
        return Document(
            document_id=uuid.uuid4().hex,
            filename=path.name,
            text=content,
            markdown=content,
            metadata={
                "tables": extract_markdown_table_records(content),
                "equations": equations,
                "equation_count": len(equations),
                "parser": "markdown",
            },
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
