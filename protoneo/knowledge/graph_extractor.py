"""Batch-parallel section-aware knowledge graph extraction.

Architecture: Process sections in batched parallel, accumulating context
between batches.

Sections within a batch share the same accumulated context snapshot from
all previously completed batches. Within a batch, sections are extracted
in parallel via asyncio.gather(), dispatched round-robin across multiple
model endpoints (e.g., 4 LM Studio inference slots + mini server).

Each batch's results merge into the KnowledgeGraph before the next batch
begins, so later batches see all entities from earlier batches.
No separate reduce step. No graph replacement.

The extraction is ontology-constrained: only entity types and relationship
types defined in the ontology are accepted. Mismatches are coerced
to fallbacks.
"""

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Callable

from pydantic import BaseModel, Field

from ..llm.client import LLMClient
from ..llm.structured import extract_json_object, sanitize_structured_text
from .metadata import extract_section_texts, extract_section_texts_md
from .graph import KnowledgeGraph
from .ontology import Ontology, ontology_to_extraction_prompt

logger = logging.getLogger("protoneo.knowledge.graph_extractor")

EventCallback = Callable[[str, dict], None] | None


# ── Label normalization ────────────────────────────────────

def _normalize_label(label: str) -> str:
    """Normalize entity labels for consistent dedup.

    Splits CamelCase into words, normalizes whitespace and casing.
    Examples:
        "StandardBaseline" -> "Standard Baseline"
        "HARDataset"       -> "HAR Dataset"
        "Standard Baselines" -> "Standard Baselines"  (no change, already spaced)
        "WISDM_Dataset"    -> "WISDM Dataset"
        "F1ScoreMetric"    -> "F1 Score Metric"
    """
    if not label or len(label) <= 2:
        return label
    # Replace underscores with spaces
    s = label.replace("_", " ")
    # Split CamelCase: insert space before uppercase letters that follow
    # a lowercase letter or precede a lowercase letter after a run of uppercase.
    # "StandardBaseline" -> "Standard Baseline"
    # "HARDataset" -> "HAR Dataset"
    # "F1ScoreMetric" -> "F1 Score Metric"
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', s)       # camelCase boundary
    s = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', s)    # ACRONYMWord boundary
    # Collapse multiple spaces
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _normalize_label_for_dedup(label: str) -> str:
    """Produce a canonical key for dedup: normalized, lowercased, no plural 's'."""
    n = _normalize_label(label).lower()
    # Strip trailing 's' for basic singular/plural matching
    # but only if the word is > 3 chars (don't strip from "bus", "gas")
    words = n.split()
    normalized_words = []
    for w in words:
        if len(w) > 3 and w.endswith('s') and not w.endswith('ss'):
            normalized_words.append(w[:-1])
        else:
            normalized_words.append(w)
    return ' '.join(normalized_words)


class GraphEntity(BaseModel):
    name: str
    type: str
    description: str = ""


class GraphRelationship(BaseModel):
    source: str
    target: str
    type: str
    description: str = ""


class ExtractedGraph(BaseModel):
    """Graph extracted from a document by the LLM."""
    entities: list[GraphEntity] = Field(default_factory=list)
    relationships: list[GraphRelationship] = Field(default_factory=list)


# ── Section extraction prompt ──────────────────────────────

_SECTION_SYSTEM = """\
You extract entities and relationships from academic paper sections.
Output concise valid JSON only. Do not emit hidden reasoning, scratchpad,
markdown, code fences, or prose."""

_SECTION_PROMPT_TEMPLATE = """\
Extract entities and relationships from section "{section_name}" of an academic paper.

{accumulated_context_block}
{ontology_guide}

RULES:
1. Extract ONLY what is explicitly stated in this section text.
2. Every entity MUST have at least one relationship. No isolated entities.
3. PRIORITIZE relationships. A graph with 5 entities and 8 relationships is better than 15 entities and 3 relationships.
4. Cross-reference entities from previous sections by exact name.
5. Use specific entity types from the ontology. Use "Concept" only as a last resort.
6. Entity names: use the paper's own terminology, under 6 words. Readable multi-word names with spaces, not CamelCase.
7. For quantitative results, include the value in the description (e.g., "3.76x speedup over dense baseline").
8. If an entity is an abbreviation, include the full form in the description.
9. Extract key data points from markdown tables as entities with relationships.
10. Extract named equations and what they compute.
11. Figure descriptions contain quantitative analysis (axes, trends, method comparisons). Extract the findings they report as Result or Metric entities.

Respond with ONLY this JSON, nothing else:
{{"entities": [{{"name": "...", "type": "...", "description": "one sentence with key details"}}], "relationships": [{{"source": "entity name", "target": "entity name", "type": "EDGE_TYPE", "description": "brief context"}}]}}

Section text ({section_name}):
{section_text}"""

# ── Legacy chunk prompt (kept for backward compat) ─────────

_CHUNK_SYSTEM = (
    "You extract entities and relationships from academic paper text. "
    "Output concise valid JSON only. Do not emit hidden reasoning, scratchpad, "
    "markdown, code fences, or prose."
)

_CHUNK_PROMPT_TEMPLATE = """\
Extract entities and relationships from this paper chunk.

{ontology_guide}

Return JSON: {{"entities": [{{"name": "...", "type": "...", "description": "..."}}], "relationships": [{{"source": "...", "target": "...", "type": "...", "description": "..."}}]}}

Only extract what is explicitly stated in this chunk. Be specific with entity names.

Text chunk:
{chunk_text}"""

_CHUNK_PROMPT_GENERIC = """\
Extract entities and relationships from this academic paper chunk.

Entity types: Method, Dataset, Metric, Result, Baseline, Concept, System, Contribution
Relationship types: USES, EVALUATES_ON, ACHIEVES, OUTPERFORMS, EXTENDS, COMPONENT_OF, SUPPORTS

Return JSON: {{"entities": [{{"name": "...", "type": "...", "description": "..."}}], "relationships": [{{"source": "...", "target": "...", "type": "...", "description": "..."}}]}}

Text chunk:
{chunk_text}"""


# ── Parsing and validation helpers ─────────────────────────

def _parse_extraction(raw: str) -> ExtractedGraph:
    """Parse LLM output into an ExtractedGraph, handling various formats."""
    if not raw or not raw.strip():
        return ExtractedGraph()

    parsed = extract_json_object(
        raw,
        required_keys={"entities", "relationships"},
        allow_thinking_json=True,
    )
    if parsed is not None:
        return ExtractedGraph(**parsed)

    raw = sanitize_structured_text(raw)

    # Try direct JSON parse
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return ExtractedGraph(**data)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Try extracting from code fence
    for fence_match in re.finditer(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL):
        try:
            data = json.loads(fence_match.group(1))
            if isinstance(data, dict):
                return ExtractedGraph(**data)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    # Try finding the largest valid JSON object
    best_data = None
    best_size = 0
    pos = 0
    while pos < len(raw):
        start = raw.find("{", pos)
        if start < 0:
            break
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = raw[start : i + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict) and len(candidate) > best_size:
                            if "entities" in data or "relationships" in data:
                                best_data = data
                                best_size = len(candidate)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
                    pos = i + 1
                    break
        else:
            break

    if best_data:
        return ExtractedGraph(**best_data)

    # Last resort: salvage truncated JSON
    salvaged = _salvage_truncated_json(raw)
    if salvaged:
        return salvaged

    logger.warning("Failed to parse extraction output (%d chars), first 300: %s", len(raw), raw[:300])
    return ExtractedGraph()


def _salvage_truncated_json(raw: str) -> ExtractedGraph | None:
    """Recover entities from truncated JSON output (LLM hit max_tokens)."""
    entities = []
    relationships = []

    entity_pattern = re.compile(
        r'\{\s*"name"\s*:\s*"([^"]+)"\s*,\s*"type"\s*:\s*"([^"]+)"\s*,\s*"description"\s*:\s*"([^"]*)"[^}]*\}',
    )

    rels_start = raw.find('"relationships"')

    for match in entity_pattern.finditer(raw):
        name, etype, desc = match.group(1), match.group(2), match.group(3)
        pos = match.start()
        if rels_start > 0 and pos > rels_start:
            relationships.append(GraphRelationship(source=name, target=etype, type=desc))
        else:
            entities.append(GraphEntity(name=name, type=etype, description=desc))

    rel_pattern = re.compile(
        r'\{\s*"source"\s*:\s*"([^"]+)"\s*,\s*"target"\s*:\s*"([^"]+)"\s*,\s*"type"\s*:\s*"([^"]+)"',
    )
    for match in rel_pattern.finditer(raw):
        relationships.append(GraphRelationship(
            source=match.group(1), target=match.group(2), type=match.group(3),
        ))

    if entities or relationships:
        logger.info("Salvaged %d entities and %d relationships from truncated JSON", len(entities), len(relationships))
        return ExtractedGraph(entities=entities, relationships=relationships)
    return None


def _validate_against_ontology(graph: ExtractedGraph, ontology: Ontology) -> ExtractedGraph:
    """Coerce extracted entity types to match ontology schema."""
    valid_types = {et.name for et in ontology.entity_types}
    if not valid_types:
        return graph

    coerced_entities = []
    for entity in graph.entities:
        if entity.type in valid_types:
            coerced_entities.append(entity)
        else:
            fallback = "Concept"
            if any(kw in entity.type.lower() for kw in ("ref", "cite", "paper", "work", "baseline")):
                fallback = "Reference"
            coerced_entities.append(GraphEntity(name=entity.name, type=fallback, description=entity.description))

    valid_rels = {rt.name for rt in ontology.edge_types} if ontology.edge_types else set()
    coerced_rels = []
    for rel in graph.relationships:
        if not valid_rels or rel.type in valid_rels:
            coerced_rels.append(rel)
        else:
            coerced_rels.append(GraphRelationship(
                source=rel.source, target=rel.target, type="RELATED_TO", description=rel.description,
            ))

    return ExtractedGraph(entities=coerced_entities, relationships=coerced_rels)


def _normalize_entities(graph: ExtractedGraph) -> ExtractedGraph:
    """Post-process extracted graph: normalize labels, deduplicate by canonical key."""
    # Build canonical name map: dedup_key -> first seen entity
    canonical: dict[str, GraphEntity] = {}
    name_remap: dict[str, str] = {}  # original name -> canonical display name

    for e in graph.entities:
        display = _normalize_label(e.name)
        key = _normalize_label_for_dedup(e.name)
        if key in canonical:
            # Keep the entity with the longer description
            existing = canonical[key]
            if len(e.description) > len(existing.description):
                existing.description = e.description
            name_remap[e.name] = existing.name
        else:
            canonical[key] = GraphEntity(name=display, type=e.type, description=e.description)
            name_remap[e.name] = display

    # Remap relationship endpoints to canonical names
    normalized_rels = []
    seen_rels: set[tuple[str, str, str]] = set()
    for r in graph.relationships:
        src = name_remap.get(r.source, _normalize_label(r.source))
        tgt = name_remap.get(r.target, _normalize_label(r.target))
        key = (src.lower(), tgt.lower(), r.type)
        if key not in seen_rels:
            normalized_rels.append(GraphRelationship(
                source=src, target=tgt, type=r.type, description=r.description,
            ))
            seen_rels.add(key)

    return ExtractedGraph(entities=list(canonical.values()), relationships=normalized_rels)


def _merge_into(base: ExtractedGraph, new: ExtractedGraph) -> ExtractedGraph:
    """Merge new entities/relationships into existing graph, deduplicating."""
    seen_entities = {_normalize_label_for_dedup(e.name) for e in base.entities}
    merged_entities = list(base.entities)
    for e in new.entities:
        key = _normalize_label_for_dedup(e.name)
        if key not in seen_entities:
            merged_entities.append(e)
            seen_entities.add(key)

    seen_rels = {(r.source.lower(), r.target.lower(), r.type) for r in base.relationships}
    merged_rels = list(base.relationships)
    for r in new.relationships:
        key = (r.source.lower(), r.target.lower(), r.type)
        if key not in seen_rels:
            merged_rels.append(r)
            seen_rels.add(key)

    return ExtractedGraph(entities=merged_entities, relationships=merged_rels)


def extracted_to_graph_data(extracted: ExtractedGraph) -> dict[str, Any]:
    """Convert ExtractedGraph into GraphPanel's expected node/edge format."""
    nodes = []
    edges = []
    name_to_uuid: dict[str, str] = {}

    for entity in extracted.entities:
        eid = uuid.uuid4().hex[:12]
        name_to_uuid[entity.name] = eid
        nodes.append({
            "uuid": eid,
            "name": entity.name,
            "labels": ["Entity", entity.type],
            "attributes": {"description": entity.description},
        })

    for rel in extracted.relationships:
        src_id = name_to_uuid.get(rel.source)
        tgt_id = name_to_uuid.get(rel.target)
        if not src_id or not tgt_id:
            continue
        edges.append({
            "source_node_uuid": src_id,
            "target_node_uuid": tgt_id,
            "name": rel.type,
            "fact_type": rel.type,
            "attributes": {"description": rel.description},
        })

    return {"nodes": nodes, "edges": edges}


def _metadata_fallback_graph(text: str) -> ExtractedGraph:
    """Build a minimal graph from paper metadata when LLM extraction fails."""
    from .metadata import extract_metadata
    meta = extract_metadata(text)
    entities = []
    relationships = []

    if meta.title:
        entities.append(GraphEntity(name=meta.title, type="Contribution", description="Paper title"))
    for sec in meta.sections:
        entities.append(GraphEntity(name=sec, type="Concept", description="Paper section"))
        if meta.title:
            relationships.append(GraphRelationship(source=meta.title, target=sec, type="CONTAINS"))
    for i in range(1, min(meta.figure_count + 1, 11)):
        entities.append(GraphEntity(name=f"Figure {i}", type="Concept", description="Figure"))
    for i in range(1, min(meta.table_count + 1, 11)):
        entities.append(GraphEntity(name=f"Table {i}", type="Concept", description="Table"))

    return ExtractedGraph(entities=entities, relationships=relationships)


def _chunk_text(text: str, chunk_size: int = 4000, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks for progressive extraction."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end < len(text):
            for sep in ["\n\n", ".\n", ". ", "\n"]:
                idx = text.rfind(sep, start + chunk_size // 2, end)
                if idx > 0:
                    end = idx + len(sep)
                    break
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


async def _llm_section_split(
    text: str,
    llm_client: LLMClient,
    model: str,
    session_id: str | None,
) -> list[tuple[str, str]]:
    """Use the LLM to identify section boundaries in unstructured paper text.

    Called when the text has no markdown headers (e.g., raw PyMuPDF output).
    Returns (section_name, section_body) tuples covering the full paper.
    """
    response = await llm_client.complete(
        model=model,
        messages=[
            {"role": "system", "content": "You identify section boundaries in academic paper text. Output JSON only."},
            {"role": "user", "content": (
                "Identify ALL section and subsection boundaries in this paper. "
                "Section numbers often appear on a separate line from their titles. "
                "Return a JSON array: [{\"number\": \"1\", \"title\": \"Introduction\", "
                "\"start_phrase\": \"unique phrase from first line of section body\"}]\n\n"
                "Include Abstract, Introduction, every numbered section/subsection, "
                "Related Work, Conclusion. Do NOT include References.\n\n"
                f"{text}"
            )},
        ],
        session_id=session_id,
        temperature=0.1,
        max_tokens=4096,
        phase_policy="fast_structured",
    )

    # Parse the section list
    try:
        raw = response.content
        raw = sanitize_structured_text(raw)
        # Find JSON array
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end > start:
            section_list = json.loads(raw[start:end + 1])
        else:
            return []
    except (json.JSONDecodeError, ValueError):
        logger.warning("Failed to parse LLM section boundaries")
        return []

    if not section_list:
        return []

    # Match start_phrases to positions in the text
    text_lower = text.lower()
    boundaries: list[tuple[str, int]] = []
    for sec in section_list:
        title = sec.get("title", "")
        number = sec.get("number", "")
        phrase = sec.get("start_phrase", "")
        name = f"{number} {title}".strip() if number else title

        pos = -1
        if phrase:
            pos = text_lower.find(phrase.lower()[:80])
        if pos < 0 and title:
            pos = text_lower.find(title.lower())
        if pos >= 0:
            boundaries.append((name, pos))

    if not boundaries:
        return []

    # Sort by position and split text
    boundaries.sort(key=lambda x: x[1])
    sections: list[tuple[str, str]] = []
    for i, (name, start_pos) in enumerate(boundaries):
        end_pos = boundaries[i + 1][1] if i + 1 < len(boundaries) else len(text)
        body = text[start_pos:end_pos].strip()
        if body:
            sections.append((name, body))

    return sections


# ── Main extraction function ──────────────────────────────

def _split_subsections(section_name: str, section_text: str) -> list[tuple[str, str]]:
    """Split a section into subsections on ### or #### headers.

    When no subsection headers are found, returns the original section
    as a single chunk. Adjacent subsections overlap by one paragraph
    to preserve context at boundaries.
    """
    header_re = re.compile(r"^(#{3,4})\s+(.+)$", re.MULTILINE)
    matches = list(header_re.finditer(section_text))
    if not matches:
        return [(section_name, section_text)]

    chunks: list[tuple[str, str]] = []
    # Text before first subsection header
    preamble = section_text[:matches[0].start()].strip()
    if preamble:
        chunks.append((section_name, preamble))

    for i, m in enumerate(matches):
        sub_name = f"{section_name} > {m.group(2).strip()}"
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section_text)
        body = section_text[start:end].strip()
        if not body:
            continue

        # Add overlap: last paragraph from previous subsection
        if i > 0 and chunks:
            prev_text = chunks[-1][1]
            last_para = prev_text.rsplit("\n\n", 1)[-1] if "\n\n" in prev_text else ""
            if last_para and len(last_para) < 500:
                body = last_para + "\n\n" + body

        chunks.append((sub_name, body))

    return chunks if chunks else [(section_name, section_text)]


async def extract_graph(
    text: str,
    llm_client: LLMClient,
    model: str = "",
    session_id: str | None = None,
    on_progress: EventCallback = None,
    ontology: Ontology | None = None,
    knowledge_graph: "KnowledgeGraph | None" = None,
    batch_size: int = 4,
    models: list[str] | None = None,
    markdown: str = "",
) -> dict[str, Any]:
    """Extract a knowledge graph from an academic paper.

    When knowledge_graph is provided, uses batch-parallel section extraction:
    sections are grouped into batches of ``batch_size`` and extracted in
    parallel within each batch via asyncio.gather(). All sections in a
    batch share the same accumulated context snapshot from prior batches.
    Multiple model endpoints can be provided via ``models`` for round-robin
    dispatch across inference slots.

    When markdown is provided, uses subsection-level chunking (### / ####
    headers) for finer-grained extraction. Also auto-creates APPEARS_IN
    edges linking each extracted entity to its source section node.

    When knowledge_graph is None, falls back to chunk-based extraction for
    backward compatibility (no reduce step, just accumulation).
    """
    # Build ontology guide
    ontology_guide = ""
    if ontology and ontology.entity_types:
        ontology_guide = ontology_to_extraction_prompt(ontology)
    if not ontology_guide:
        ontology_guide = (
            "Entity types: Method, Dataset, Metric, Result, Baseline, Concept, System, Contribution\n"
            "Relationship types: USES, EVALUATES_ON, ACHIEVES, OUTPERFORMS, EXTENDS, COMPONENT_OF, SUPPORTS"
        )

    # ── Batch-parallel section path (KnowledgeGraph provided) ──
    if knowledge_graph is not None:
        # Prefer markdown section texts when available
        if markdown:
            section_texts = extract_section_texts_md(markdown)
        else:
            section_texts = extract_section_texts(text)
        sections = [(name, body) for name, body in section_texts.items() if body.strip()]

        # If section detection found only 1 section (unstructured text from PyMuPDF),
        # use the LLM to identify section boundaries before extraction.
        if len(sections) <= 1 and len(text) > 5000:
            logger.info("No section structure found, using LLM to identify sections...")
            llm_sections = await _llm_section_split(text, llm_client, model, session_id)
            if len(llm_sections) > 1:
                sections = llm_sections
                logger.info("LLM identified %d sections", len(sections))

        total_sections = len(sections)

        # Skip References section from extraction (it's bibliographic, not semantic)
        sections = [(n, b) for n, b in sections if not n.lower().startswith("reference")]

        # Subsection-level chunking when markdown is available
        if markdown:
            expanded: list[tuple[str, str]] = []
            for sec_name, sec_body in sections:
                expanded.extend(_split_subsections(sec_name, sec_body))
            sections = expanded

        logger.info(
            "Batch-parallel extraction: %d sections, batch_size=%d [%s]",
            len(sections), batch_size,
            ", ".join(name for name, _ in sections),
        )

        # Round-robin model list for multi-endpoint dispatch
        model_list = models if models else [model]

        # Reuse the full ontology guide (with descriptions, attributes, examples)
        # built at the top of the function. This ensures the batch path gets
        # the same rich extraction guidance as the chunk-based fallback path.
        ont_guide = ontology_guide

        async def _extract_one_section(
            section_name: str, section_text: str,
            accumulated_context: str, use_model: str,
        ) -> tuple[str, ExtractedGraph]:
            """Extract entities from a single section (full text, no truncation)."""
            acc_block = ""
            if accumulated_context:
                acc_block = f"## Previously extracted entities (from earlier sections):\n{accumulated_context}\n"

            prompt = _SECTION_PROMPT_TEMPLATE.format(
                section_name=section_name,
                accumulated_context_block=acc_block,
                ontology_guide=ont_guide,
                section_text=section_text,
            )

            try:
                response = await llm_client.complete(
                    model=use_model,
                    messages=[
                        {"role": "system", "content": _SECTION_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    session_id=session_id,
                    temperature=0.2,
                    max_tokens=8192,
                    phase_policy="fast_structured",
                )
                result = _parse_extraction(response.content)
                result = _normalize_entities(result)
                if ontology:
                    result = _validate_against_ontology(result, ontology)
                return section_name, result
            except Exception as e:
                logger.warning("Section '%s' extraction failed: %s", section_name[:30], e)
                return section_name, ExtractedGraph()

        def _ingest_result(section_name: str, section_graph: ExtractedGraph) -> int:
            """Merge extraction results into knowledge_graph. Returns nodes added.

            Auto-creates APPEARS_IN edges from each new entity to the
            section node, bridging the structural and semantic subgraphs.
            """
            nodes_before = len(knowledge_graph.nodes)
            name_to_id = {}
            new_node_ids = []
            for e in section_graph.entities:
                node = knowledge_graph.add_node(
                    label=e.name,
                    node_type=e.type,
                    description=e.description,
                    source_section=section_name,
                )
                name_to_id[e.name] = node.id
                new_node_ids.append(node.id)

            label_to_id = {n.label.lower(): n.id for n in knowledge_graph.nodes}
            for r in section_graph.relationships:
                src_id = label_to_id.get(r.source.lower()) or name_to_id.get(r.source)
                tgt_id = label_to_id.get(r.target.lower()) or name_to_id.get(r.target)
                if src_id and tgt_id:
                    knowledge_graph.add_edge(
                        source_id=src_id, target_id=tgt_id,
                        edge_type=r.type, description=r.description,
                        source_text=section_name,
                    )

            # Auto-bridge: create APPEARS_IN edges from new entities to section node.
            # Use the base section name (before " > subsection") for matching.
            base_section = section_name.split(" > ")[0] if " > " in section_name else section_name
            sec_node = None
            for n in knowledge_graph.nodes:
                if n.node_type == "Section" and (
                    n.label.lower() == base_section.lower()
                    or base_section.lower() in n.label.lower()
                    or n.label.lower() in base_section.lower()
                ):
                    sec_node = n
                    break
            if sec_node:
                _STRUCTURAL = {"Paper", "Section", "Diagram", "Table", "Reference", "Equation"}
                for nid in new_node_ids:
                    node = knowledge_graph.node_by_id(nid)
                    if node and node.node_type not in _STRUCTURAL:
                        knowledge_graph.add_edge(
                            source_id=nid,
                            target_id=sec_node.id,
                            edge_type="APPEARS_IN",
                            description=f"extracted from {section_name}",
                        )

            return len(knowledge_graph.nodes) - nodes_before

        # Process sections in batches
        processed = 0
        for batch_start in range(0, len(sections), batch_size):
            batch = sections[batch_start:batch_start + batch_size]

            # Snapshot context from all previously processed sections
            accumulated_ctx = knowledge_graph.get_accumulated_context()

            if on_progress:
                on_progress("graph_progress", {
                    "phase": "extracting",
                    "message": f"Extracting batch {batch_start // batch_size + 1} ({len(batch)} sections)...",
                    "node_count": len(knowledge_graph.nodes),
                    "edge_count": len(knowledge_graph.edges),
                    "section": processed + 1,
                    "total_sections": total_sections,
                })

            # Launch all sections in this batch in parallel
            tasks = []
            for i, (sec_name, sec_text) in enumerate(batch):
                use_model = model_list[i % len(model_list)]
                tasks.append(_extract_one_section(sec_name, sec_text, accumulated_ctx, use_model))

            results = await asyncio.gather(*tasks)

            # Ingest all results from this batch into the graph
            batch_nodes = 0
            for sec_name, sec_graph in results:
                added = _ingest_result(sec_name, sec_graph)
                batch_nodes += added
                processed += 1
                if on_progress:
                    on_progress("extraction_section_done", {
                        "section": sec_name,
                        "nodes_added": added,
                        "processed": processed,
                        "total_sections": total_sections,
                    })

            logger.info(
                "Batch %d: %d sections, +%d nodes (total: %d nodes, %d edges)",
                batch_start // batch_size + 1, len(batch), batch_nodes,
                len(knowledge_graph.nodes), len(knowledge_graph.edges),
            )

            # Emit live graph update after each batch
            if on_progress:
                d3 = knowledge_graph.to_d3_format()
                on_progress("graph_updated", {
                    "node_count": len(d3["nodes"]),
                    "edge_count": len(d3["edges"]),
                    "nodes": d3["nodes"],
                    "edges": d3["edges"],
                    "section": processed,
                    "total_sections": total_sections,
                })

        knowledge_graph.update_stats()
        graph_data = knowledge_graph.to_d3_format()

        if on_progress:
            on_progress("graph_progress", {
                "phase": "complete",
                "message": f"Graph complete: {len(knowledge_graph.nodes)} nodes, {len(knowledge_graph.edges)} edges",
                "node_count": len(knowledge_graph.nodes),
                "edge_count": len(knowledge_graph.edges),
            })

        logger.info("Final graph: %d nodes, %d edges", len(knowledge_graph.nodes), len(knowledge_graph.edges))
        return graph_data

    # ── Fallback: chunk-based extraction (no KnowledgeGraph) ──
    chunks = _chunk_text(text)
    total_chunks = len(chunks)
    logger.info("Chunk-based fallback extraction: %d chunks", total_chunks)

    accumulated = ExtractedGraph()

    for i, chunk in enumerate(chunks):
        if on_progress:
            on_progress("graph_progress", {
                "phase": "extracting",
                "message": f"Extracting chunk {i + 1}/{total_chunks}...",
                "chunk": i + 1,
                "total_chunks": total_chunks,
            })

        if ontology and ontology.entity_types:
            prompt = _CHUNK_PROMPT_TEMPLATE.format(
                ontology_guide=ontology_guide,
                chunk_text=chunk,
            )
        else:
            prompt = _CHUNK_PROMPT_GENERIC.format(chunk_text=chunk)

        try:
            response = await llm_client.complete(
                model=model,
                messages=[
                    {"role": "system", "content": _CHUNK_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                session_id=session_id,
                temperature=0.2,
                max_tokens=4096,
                phase_policy="fast_structured",
            )

            chunk_graph = _parse_extraction(response.content)
            if ontology:
                chunk_graph = _validate_against_ontology(chunk_graph, ontology)
            accumulated = _merge_into(accumulated, chunk_graph)

            logger.info(
                "Chunk %d/%d: +%d entities, +%d rels (accumulated: %d entities, %d rels)",
                i + 1, total_chunks,
                len(chunk_graph.entities), len(chunk_graph.relationships),
                len(accumulated.entities), len(accumulated.relationships),
            )
        except Exception as e:
            logger.warning("Chunk %d/%d extraction failed: %s", i + 1, total_chunks, e)
            continue

    # Fallback to metadata if nothing was extracted
    if not accumulated.entities:
        logger.warning("All chunks produced 0 entities, falling back to metadata graph")
        accumulated = _metadata_fallback_graph(text)

    graph_data = extracted_to_graph_data(accumulated)

    if on_progress:
        on_progress("graph_progress", {
            "phase": "complete",
            "message": f"Graph complete: {len(graph_data['nodes'])} nodes, {len(graph_data['edges'])} edges",
            "node_count": len(graph_data["nodes"]),
            "edge_count": len(graph_data["edges"]),
        })
        on_progress("graph_updated", {
            "node_count": len(graph_data["nodes"]),
            "edge_count": len(graph_data["edges"]),
            "nodes": graph_data["nodes"],
            "edges": graph_data["edges"],
        })

    logger.info("Final graph: %d nodes, %d edges", len(graph_data["nodes"]), len(graph_data["edges"]))
    return graph_data
