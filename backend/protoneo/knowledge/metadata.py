"""Extract structured metadata from academic paper text.

Pulls title, abstract, section headers, figure/table counts, and reference
list using heuristic patterns. No LLM calls required.
"""

import re
from pydantic import BaseModel, Field


class PaperMetadata(BaseModel):
    """Structured metadata extracted from a parsed academic paper."""

    title: str = ""
    abstract: str = ""
    sections: list[str] = Field(default_factory=list)
    figure_count: int = 0
    table_count: int = 0
    reference_count: int = 0
    references: list[str] = Field(default_factory=list)
    estimated_word_count: int = 0
    citation_markers: list[dict] = Field(default_factory=list)  # [{marker: "[1]", position: 123}]
    equation_labels: list[str] = Field(default_factory=list)  # ["Eq. 1", "Theorem 2"]
    section_texts: dict[str, str] = Field(default_factory=dict)  # heading -> body text


# Numbered section: "1 Introduction", "3.1 Pivot Selection", "II. Related Work"
# The number must be an integer or dotted integer (not a decimal like 66.07)
_NUMBERED_SECTION_RE = re.compile(
    r"^\s*"
    r"(?:"
    r"(\d+(?:\.\d+)*)\.\s+"       # "1. ", "3.1. ", "5.2.1. " (dot-terminated)
    r"|(\d+(?:\.\d+)*)\s+"        # "1 ", "3.1 " (space-terminated, no trailing dot)
    r"|([IVX]+\.?\s+)"            # "II. ", "IV "
    r")"
    r"([A-Z][A-Za-z][\w\s:&,/()-]{1,58})"  # heading must start with 2+ alpha chars
    r"\s*$",
    re.MULTILINE,
)

# All-caps section: "ABSTRACT", "INTRODUCTION", "REFERENCES"
_ALLCAPS_SECTION_RE = re.compile(
    r"^\s*([A-Z][A-Z\s]{2,40})\s*$",
    re.MULTILINE,
)

# Known section names (no number required)
_KNOWN_SECTIONS = {
    "abstract", "introduction", "background", "related work",
    "methodology", "methods", "method", "approach", "design",
    "implementation", "evaluation", "experiments", "results",
    "discussion", "conclusion", "conclusions", "future work",
    "references", "bibliography", "acknowledgments", "acknowledgements",
    "appendix", "appendices",
}

# Abstract block: "Abstract" header followed by text until next section heading.
_ABSTRACT_RE = re.compile(
    r"(?:^|\n)\s*(?:ABSTRACT|Abstract)\s*\n(.*?)(?=\n\s*(?:\d+\.?\s+|[IVX]+\.?\s+)?[A-Z][A-Za-z\s]{2,40}\s*\n)",
    re.DOTALL,
)

# Alternate abstract pattern for inline abstracts (single paragraph after "Abstract").
_ABSTRACT_INLINE_RE = re.compile(
    r"(?:^|\n)\s*(?:ABSTRACT|Abstract)[:\s—\-]*\n?((?:(?!\n\s*(?:1\.?\s|I\.?\s|Introduction|INTRODUCTION)).)+)",
    re.DOTALL,
)

# Figure references: "Figure 1", "Fig. 3", "FIGURE 2"
_FIGURE_RE = re.compile(r"\b(?:Figure|Fig\.?)\s+(\d+)", re.IGNORECASE)

# Table references: "Table 1", "TABLE 3"
_TABLE_RE = re.compile(r"\bTable\s+(\d+)", re.IGNORECASE)

# Numbered references: "[1]", "[23]"
_REF_NUM_RE = re.compile(r"\[(\d+)\]")

# Full reference lines in the references section.
_REF_LINE_RE = re.compile(r"^\s*\[(\d+)\]\s*(.+)", re.MULTILINE)

# Citation markers: [1], [2-5], [1,3,5]
_CITE_BRACKET_RE = re.compile(r'\[(\d+(?:[,\s]*\d+)*(?:\s*[-–]\s*\d+)?)\]')

# Author-year citations: (Author, 2024), (Author et al., 2023)
_CITE_AUTHOR_RE = re.compile(r'\(([A-Z][a-z]+(?:\s+et\s+al\.?)?[\s,]+\d{4}[a-z]?)\)')

# Equation labels: Eq. 1, Equation 2, Theorem 3, Lemma 1, Corollary 2, Definition 4
_EQUATION_LABEL_RE = re.compile(
    r'\b((?:Eq(?:uation)?\.?\s*\d+|Theorem\s+\d+|Lemma\s+\d+|Corollary\s+\d+|'
    r'Definition\s+\d+|Proposition\s+\d+|Property\s+\d+))',
    re.IGNORECASE,
)

# Parenthetical equation numbers at line end: "(1)", "(2)" on lines with math-like content
_EQUATION_PAREN_RE = re.compile(
    r'(?:^|\n)[^\n]*[=∑∏∫∂∇≤≥±×÷∈∀∃αβγδεζηθλμπσφωΩ][^\n]*\((\d+)\)\s*(?:\n|$)'
)

# Lines that are clearly not titles (too short, all caps boilerplate, etc.).
_TITLE_SKIP = re.compile(
    r"^(?:\d+|[IVX]+\.?|(?:proceedings|conference|workshop|journal|vol\.|pp\.|"
    r"ieee|acm|springer|permission|copyright|https?://|doi:|arxiv|"
    r"submission|anonymous|hpdc|sc\s|ics\s|sigmod|vldb|osdi|sosp|"
    r"usenix|nips|icml|neurips|iclr|aaai|cvpr|eccv|iccv)\b)",
    re.IGNORECASE,
)


def _extract_title(text: str) -> str:
    """Heuristic title extraction from the first lines of a paper.

    The title is typically the first non-trivial line(s) before the author block
    or abstract. We look for the longest substantial line in the first 20 lines
    that isn't a known boilerplate pattern.
    """
    lines = text.split("\n")[:20]
    candidates: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if candidates:
                break  # blank line after title block ends it
            continue
        # Check for "Abstract" before length filter (it can be short)
        if stripped.lower() in ("abstract", "abstract."):
            break
        # Skip short lines (page numbers, headers)
        if len(stripped) < 10:
            continue
        # Skip boilerplate. If we already have title candidates, this means
        # we hit author/metadata lines after the title, so stop.
        if _TITLE_SKIP.match(stripped):
            if candidates:
                break
            continue
        # Skip lines that look like author/affiliation blocks
        if re.match(r"^(?:Anonymous\s+Author|[A-Z][a-z]+\s+[A-Z][a-z]+(?:\s*[,(]|\s+and\s))", stripped):
            if candidates:
                break  # author line after title
            continue
        # Skip submission metadata
        if re.match(r"^Submission\s+Id", stripped, re.IGNORECASE):
            if candidates:
                break
            continue
        if "@" in stripped or "university" in stripped.lower():
            if candidates:
                break
            continue
        # Stop if this line looks like the start of a paragraph (lowercase continuation)
        if candidates and re.match(r"^[a-z]", stripped):
            break
        # Stop if line contains sentence-ending punctuation mid-text (abstract body)
        if candidates and re.search(r"\.\s+[A-Z]", stripped):
            break
        candidates.append(stripped)
        # Stop if title is getting too long (probably absorbing abstract)
        if sum(len(c) for c in candidates) > 150:
            break

    if not candidates:
        return ""

    # Join multi-line titles (common in two-column PDFs)
    title = " ".join(candidates)
    # Clean up PDF artifacts
    title = re.sub(r"\s+", " ", title).strip()
    if len(title) > 200:
        title = title[:200].rsplit(" ", 1)[0]
    return title


def _extract_abstract(text: str) -> str:
    """Extract abstract text from the paper."""
    match = _ABSTRACT_RE.search(text)
    if not match:
        match = _ABSTRACT_INLINE_RE.search(text)
    if not match:
        return ""
    abstract = match.group(1).strip()
    # Clean up newlines within the abstract paragraph
    abstract = re.sub(r"\s*\n\s*", " ", abstract)
    abstract = re.sub(r"\s+", " ", abstract)
    # Truncate if absurdly long (probably grabbed too much)
    if len(abstract) > 3000:
        abstract = abstract[:3000].rsplit(".", 1)[0] + "."
    return abstract


def _extract_sections(text: str) -> list[str]:
    """Extract ordered section headings from the paper.

    Uses strict matching: requires numbered prefix OR all-caps OR known name.
    Applies a fallback heuristic if zero sections found.
    """
    sections: list[str] = []
    seen_lower: set[str] = set()

    # Words that are NOT section headings when standing alone (table headers, method names)
    _NOISE_WORDS = {
        "method", "model", "dense", "blast", "monarch", "low-rank",
        "flop", "accuracy", "results", "loss", "score", "baseline",
        "imagenet", "mnist", "cifar", "indices", "layer",
    }

    # Also reject known-section pass matches for these noise words
    _KNOWN_NOISE = _NOISE_WORDS | {"method", "results"}

    # Pass 1: numbered sections (most reliable)
    for match in _NUMBERED_SECTION_RE.finditer(text):
        heading = match.group(0).strip().rstrip(".:;")
        # Extract the heading text (last capture group)
        heading_text = (match.group(4) or "").strip()
        if not heading_text or len(heading_text) < 3:
            continue
        key = heading.lower()
        if key in seen_lower:
            continue
        # Skip figure/table captions
        if re.match(r"(?i)^[\d.\s]*(figure|fig\.|table)\s+\d", heading):
            continue
        # Skip if heading text is a single noise word
        if heading_text.lower().strip() in _NOISE_WORDS:
            continue
        seen_lower.add(key)
        # Also mark the heading text alone as seen to prevent Pass 3 duplicates
        seen_lower.add(heading_text.lower().strip())
        sections.append(heading)

    # Pass 2: all-caps headings (only known section names to avoid table headers)
    for match in _ALLCAPS_SECTION_RE.finditer(text):
        heading = match.group(1).strip()
        if len(heading) < 5 or len(heading) > 40:
            continue
        key = heading.lower().strip()
        if key in seen_lower:
            continue
        # Only accept all-caps if it is a known section name
        if key in _KNOWN_SECTIONS:
            seen_lower.add(key)
            sections.append(heading)

    # Pass 3: known section names at start of line (skip if already captured by numbered pass)
    # Also skip single-word names that are common table headers
    _SKIP_STANDALONE = {"method", "model", "results", "dense"}
    for name in _KNOWN_SECTIONS:
        if name in seen_lower or name in _SKIP_STANDALONE:
            continue
        pattern = re.compile(
            r"^\s*" + re.escape(name.title()) + r"\s*$",
            re.MULTILINE | re.IGNORECASE,
        )
        match = pattern.search(text)
        if match:
            heading = match.group(0).strip()
            key = heading.lower()
            if key not in seen_lower:
                seen_lower.add(key)
                sections.append(heading)

    # Sort sections by position in text
    section_positions = []
    for sec in sections:
        pos = text.find(sec)
        if pos >= 0:
            section_positions.append((sec, pos))
    section_positions.sort(key=lambda x: x[1])
    sections = [s for s, _ in section_positions]

    # Deduplicate sections that overlap in position (e.g., "Introduction" and "1 Introduction")
    deduped = []
    prev_pos = -100
    for sec, pos in section_positions:
        if pos - prev_pos < 20 and deduped:
            # Prefer the longer/numbered variant
            if len(sec) > len(deduped[-1]):
                deduped[-1] = sec
        else:
            deduped.append(sec)
        prev_pos = pos
    sections = deduped

    # Sanity check: if we got 0 sections, fall back to permissive regex
    if not sections:
        old_re = re.compile(
            r"^(?:\d+\.?\s+)([A-Z][A-Za-z\s:&,/-]{4,60})\s*$",
            re.MULTILINE,
        )
        for match in old_re.finditer(text):
            heading = match.group(0).strip().rstrip(".:;")
            key = heading.lower()
            if key not in seen_lower and 5 <= len(heading) <= 60:
                seen_lower.add(key)
                sections.append(heading)
        if len(sections) > 20:
            sections = sections[:20]

    return sections


def _extract_references(text: str) -> tuple[int, list[str]]:
    """Extract reference count and individual reference strings.

    Returns (count, list_of_reference_strings).
    """
    # Find the references section
    ref_start = None
    for pattern in [
        re.compile(r"\n\s*(?:REFERENCES|References|BIBLIOGRAPHY|Bibliography)\s*\n"),
    ]:
        m = pattern.search(text)
        if m:
            ref_start = m.end()
            break

    if ref_start is None:
        # Fall back to counting unique [N] markers
        nums = set(int(n) for n in _REF_NUM_RE.findall(text))
        return len(nums), []

    ref_text = text[ref_start:]
    refs: list[str] = []
    for m in _REF_LINE_RE.finditer(ref_text):
        ref_line = m.group(2).strip()
        ref_line = re.sub(r"\s+", " ", ref_line)
        if len(ref_line) > 15:
            refs.append(f"[{m.group(1)}] {ref_line}")

    if refs:
        return len(refs), refs

    # Fallback: count unique [N] across the whole paper
    nums = set(int(n) for n in _REF_NUM_RE.findall(text))
    return len(nums), []


def _count_figures(text: str) -> int:
    """Count unique figure numbers referenced in the text."""
    nums = set(int(n) for n in _FIGURE_RE.findall(text))
    return len(nums)


def _count_tables(text: str) -> int:
    """Count unique table numbers referenced in the text."""
    nums = set(int(n) for n in _TABLE_RE.findall(text))
    return len(nums)


def extract_citation_markers(text: str) -> list[dict]:
    """Extract all citation markers with positions.

    Returns list of {marker, position, type} dicts.
    type is "bracket" for [N] style or "author_year" for (Author, Year) style.
    """
    markers = []
    seen = set()
    for m in _CITE_BRACKET_RE.finditer(text):
        marker = m.group(0)
        if marker not in seen:
            markers.append({"marker": marker, "position": m.start(), "type": "bracket"})
            seen.add(marker)
    for m in _CITE_AUTHOR_RE.finditer(text):
        marker = m.group(0)
        if marker not in seen:
            markers.append({"marker": marker, "position": m.start(), "type": "author_year"})
            seen.add(marker)
    return markers


def extract_equation_labels(text: str) -> list[str]:
    """Extract all unique labeled equations/theorems/lemmas."""
    labels = []
    seen_lower = set()
    # Named labels: Eq. 1, Theorem 2, Lemma 3
    for m in _EQUATION_LABEL_RE.finditer(text):
        label = m.group(1).strip()
        key = label.lower()
        if key not in seen_lower:
            labels.append(label)
            seen_lower.add(key)
    # Parenthetical equation numbers: (1), (2) on math-like lines
    for m in _EQUATION_PAREN_RE.finditer(text):
        num = m.group(1)
        label = f"Equation ({num})"
        key = label.lower()
        if key not in seen_lower:
            labels.append(label)
            seen_lower.add(key)
    return labels


def extract_metadata(text: str) -> PaperMetadata:
    """Extract structured metadata from academic paper text."""
    ref_count, refs = _extract_references(text)
    word_count = len(text.split())
    section_texts = extract_section_texts(text)

    return PaperMetadata(
        title=_extract_title(text),
        abstract=_extract_abstract(text),
        sections=_extract_sections(text),
        figure_count=_count_figures(text),
        table_count=_count_tables(text),
        reference_count=ref_count,
        references=refs[:50],
        estimated_word_count=word_count,
        citation_markers=extract_citation_markers(text),
        equation_labels=extract_equation_labels(text),
        section_texts=section_texts,
    )


def extract_section_texts(text: str) -> dict[str, str]:
    """Extract section heading to section body text mapping."""
    sections = _extract_sections(text)
    if not sections:
        return {"Full Paper": text}

    section_texts: dict[str, str] = {}
    section_positions: list[tuple[str, int]] = []

    for sec in sections:
        pos = text.find(sec)
        if pos >= 0:
            section_positions.append((sec, pos))

    section_positions.sort(key=lambda x: x[1])

    for i, (heading, start) in enumerate(section_positions):
        if i + 1 < len(section_positions):
            end = section_positions[i + 1][1]
        else:
            end = len(text)
        body = text[start + len(heading):end].strip()
        section_texts[heading] = body

    # Merge tiny sections into preceding section
    merged: dict[str, str] = {}
    prev_key = None
    for key, body in section_texts.items():
        if prev_key is not None and len(body) < 200:
            merged[prev_key] += "\n\n" + key + "\n" + body
        else:
            merged[key] = body
            prev_key = key

    return merged


def build_structural_graph(metadata: "PaperMetadata") -> dict:
    """Build a vanilla structural graph from paper metadata.

    No LLM calls. Pure structure: paper title as root, sections as children,
    figures and tables as leaf nodes. This is Phase 0 output.

    Returns GraphPanel-compatible format with nodes and edges.
    """
    import uuid as _uuid

    nodes = []
    edges = []

    # Root node: paper title
    root_id = "paper-root"
    nodes.append({
        "uuid": root_id,
        "name": metadata.title or "Untitled Paper",
        "labels": ["Entity", "Paper"],
        "attributes": {
            "word_count": str(metadata.estimated_word_count),
            "reference_count": str(metadata.reference_count),
        },
    })

    # Abstract node
    if metadata.abstract:
        abs_id = _uuid.uuid4().hex[:12]
        nodes.append({
            "uuid": abs_id,
            "name": "Abstract",
            "labels": ["Entity", "Section"],
            "attributes": {"text_preview": metadata.abstract[:200]},
        })
        edges.append({
            "source_node_uuid": root_id,
            "target_node_uuid": abs_id,
            "name": "HAS_SECTION",
            "fact_type": "HAS_SECTION",
            "attributes": {"order": "0"},
        })

    # Section nodes
    for i, sec in enumerate(metadata.sections):
        sec_id = _uuid.uuid4().hex[:12]
        nodes.append({
            "uuid": sec_id,
            "name": sec,
            "labels": ["Entity", "Section"],
            "attributes": {"order": str(i + 1)},
        })
        edges.append({
            "source_node_uuid": root_id,
            "target_node_uuid": sec_id,
            "name": "HAS_SECTION",
            "fact_type": "HAS_SECTION",
            "attributes": {"order": str(i + 1)},
        })

    # Figure nodes
    for i in range(1, min(metadata.figure_count + 1, 16)):
        fig_id = _uuid.uuid4().hex[:12]
        nodes.append({
            "uuid": fig_id,
            "name": f"Figure {i}",
            "labels": ["Entity", "Diagram"],
            "attributes": {},
        })
        edges.append({
            "source_node_uuid": root_id,
            "target_node_uuid": fig_id,
            "name": "CONTAINS",
            "fact_type": "CONTAINS",
            "attributes": {},
        })

    # Table nodes
    for i in range(1, min(metadata.table_count + 1, 16)):
        tbl_id = _uuid.uuid4().hex[:12]
        nodes.append({
            "uuid": tbl_id,
            "name": f"Table {i}",
            "labels": ["Entity", "Table"],
            "attributes": {},
        })
        edges.append({
            "source_node_uuid": root_id,
            "target_node_uuid": tbl_id,
            "name": "CONTAINS",
            "fact_type": "CONTAINS",
            "attributes": {},
        })

    return {"nodes": nodes, "edges": edges}
