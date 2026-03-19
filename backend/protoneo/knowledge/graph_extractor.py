"""Batch-parallel section-aware knowledge graph extraction from academic papers.

Architecture: Process sections in batched parallel, accumulating context
between batches.

Sections within a batch share the same accumulated context snapshot from
all previously completed batches. Within a batch, sections are extracted
in parallel via asyncio.gather(), dispatched round-robin across multiple
model endpoints (e.g., 4 LM Studio inference slots + mini server).

Each batch's results merge into the PaperGraph before the next batch
begins, so later batches see all entities from earlier batches.
No separate reduce step. No graph replacement.

The extraction is ontology-constrained: only entity types and relationship
types defined in the paper ontology are accepted. Mismatches are coerced
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
from .metadata import extract_section_texts, extract_section_texts_md
from .paper_graph import PaperGraph
from .paper_ontology import PaperOntology, ontology_to_extraction_prompt

logger = logging.getLogger("protoneo.knowledge.graph_extractor")

EventCallback = Callable[[str, dict], None] | None


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
    """Graph extracted from a paper by the LLM."""
    entities: list[GraphEntity] = Field(default_factory=list)
    relationships: list[GraphRelationship] = Field(default_factory=list)


# ── Section extraction prompt ──────────────────────────────

_SECTION_SYSTEM = """\
You extract entities and relationships from academic paper sections.
RESPOND WITH VALID JSON ONLY. No thinking, no markdown, no explanation.
Your entire response must be a single JSON object."""

_SECTION_PROMPT_TEMPLATE = """\
Extract entities and relationships from section "{section_name}" of an academic paper.

{accumulated_context_block}
{ontology_guide}

RULES:
1. Extract ONLY what is explicitly stated in this section text.
2. Every entity MUST have at least one relationship. No isolated entities.
3. PRIORITIZE relationships. A graph with 5 entities and 8 relationships is better than 15 entities and 3 relationships.
4. When this section mentions an entity from a previous section, create a cross-reference relationship to it (use the exact name from the "Previously extracted" list above).
5. Use specific entity types from the ontology. Use "Concept" only as a last resort.
6. Entity names: use the paper's own terminology, under 6 words.
7. For quantitative results, include the value in the description (e.g., "3.76x speedup over dense baseline").
8. If the text contains markdown tables, extract the key data points as entities with relationships.
9. If the text contains equations or formulas, extract the named equation and what it computes.
10. If the text contains figure/table captions, extract what the figure/table shows.

Respond with ONLY this JSON, nothing else:
{{"entities": [{{"name": "...", "type": "...", "description": "one sentence with key details"}}], "relationships": [{{"source": "entity name", "target": "entity name", "type": "EDGE_TYPE", "description": "brief context"}}]}}

Section text ({section_name}):
{section_text}"""

# ── Legacy chunk prompt (kept for backward compat) ─────────

_CHUNK_SYSTEM = "You extract entities and relationships from academic paper text. Always respond with valid JSON only. No markdown, no explanation."

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


def _validate_against_ontology(graph: ExtractedGraph, ontology: PaperOntology) -> ExtractedGraph:
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


def _merge_into(base: ExtractedGraph, new: ExtractedGraph) -> ExtractedGraph:
    """Merge new entities/relationships into existing graph, deduplicating."""
    seen_entities = {e.name.lower() for e in base.entities}
    merged_entities = list(base.entities)
    for e in new.entities:
        if e.name.lower() not in seen_entities:
            merged_entities.append(e)
            seen_entities.add(e.name.lower())

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


async def extract_paper_graph(
    text: str,
    llm_client: LLMClient,
    model: str = "",
    session_id: str | None = None,
    on_progress: EventCallback = None,
    ontology: PaperOntology | None = None,
    paper_graph: "PaperGraph | None" = None,
    batch_size: int = 4,
    models: list[str] | None = None,
    markdown: str = "",
) -> dict[str, Any]:
    """Extract a knowledge graph from an academic paper.

    When paper_graph is provided, uses batch-parallel section extraction:
    sections are grouped into batches of ``batch_size`` and extracted in
    parallel within each batch via asyncio.gather(). All sections in a
    batch share the same accumulated context snapshot from prior batches.
    Multiple model endpoints can be provided via ``models`` for round-robin
    dispatch across inference slots.

    When markdown is provided, uses subsection-level chunking (### / ####
    headers) for finer-grained extraction. Also auto-creates APPEARS_IN
    edges linking each extracted entity to its source section node.

    When paper_graph is None, falls back to chunk-based extraction for
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

    # ── Batch-parallel section path (PaperGraph provided) ──
    if paper_graph is not None:
        # Prefer markdown section texts when available
        if markdown:
            section_texts = extract_section_texts_md(markdown)
        else:
            section_texts = extract_section_texts(text)
        sections = [(name, body) for name, body in section_texts.items() if body.strip()]
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
                )
                result = _parse_extraction(response.content)
                if ontology:
                    result = _validate_against_ontology(result, ontology)
                return section_name, result
            except Exception as e:
                logger.warning("Section '%s' extraction failed: %s", section_name[:30], e)
                return section_name, ExtractedGraph()

        def _ingest_result(section_name: str, section_graph: ExtractedGraph) -> int:
            """Merge extraction results into paper_graph. Returns nodes added.

            Auto-creates APPEARS_IN edges from each new entity to the
            section node, bridging the structural and semantic subgraphs.
            """
            nodes_before = len(paper_graph.nodes)
            name_to_id = {}
            new_node_ids = []
            for e in section_graph.entities:
                node = paper_graph.add_node(
                    label=e.name,
                    node_type=e.type,
                    description=e.description,
                    source_section=section_name,
                )
                name_to_id[e.name] = node.id
                new_node_ids.append(node.id)

            label_to_id = {n.label.lower(): n.id for n in paper_graph.nodes}
            for r in section_graph.relationships:
                src_id = label_to_id.get(r.source.lower()) or name_to_id.get(r.source)
                tgt_id = label_to_id.get(r.target.lower()) or name_to_id.get(r.target)
                if src_id and tgt_id:
                    paper_graph.add_edge(
                        source_id=src_id, target_id=tgt_id,
                        edge_type=r.type, description=r.description,
                        source_text=section_name,
                    )

            # Auto-bridge: create APPEARS_IN edges from new entities to section node.
            # Use the base section name (before " > subsection") for matching.
            base_section = section_name.split(" > ")[0] if " > " in section_name else section_name
            sec_node = None
            for n in paper_graph.nodes:
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
                    node = paper_graph.node_by_id(nid)
                    if node and node.node_type not in _STRUCTURAL:
                        paper_graph.add_edge(
                            source_id=nid,
                            target_id=sec_node.id,
                            edge_type="APPEARS_IN",
                            description=f"extracted from {section_name}",
                        )

            return len(paper_graph.nodes) - nodes_before

        # Process sections in batches
        processed = 0
        for batch_start in range(0, len(sections), batch_size):
            batch = sections[batch_start:batch_start + batch_size]

            # Snapshot context from all previously processed sections
            accumulated_ctx = paper_graph.get_accumulated_context()

            if on_progress:
                on_progress("graph_progress", {
                    "phase": "extracting",
                    "message": f"Extracting batch {batch_start // batch_size + 1} ({len(batch)} sections)...",
                    "node_count": len(paper_graph.nodes),
                    "edge_count": len(paper_graph.edges),
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

            logger.info(
                "Batch %d: %d sections, +%d nodes (total: %d nodes, %d edges)",
                batch_start // batch_size + 1, len(batch), batch_nodes,
                len(paper_graph.nodes), len(paper_graph.edges),
            )

            # Emit live graph update after each batch
            if on_progress:
                d3 = paper_graph.to_d3_format()
                on_progress("graph_updated", {
                    "node_count": len(d3["nodes"]),
                    "edge_count": len(d3["edges"]),
                    "nodes": d3["nodes"],
                    "edges": d3["edges"],
                    "section": processed,
                    "total_sections": total_sections,
                })

        paper_graph.update_stats()
        graph_data = paper_graph.to_d3_format()

        if on_progress:
            on_progress("graph_progress", {
                "phase": "complete",
                "message": f"Graph complete: {len(paper_graph.nodes)} nodes, {len(paper_graph.edges)} edges",
                "node_count": len(paper_graph.nodes),
                "edge_count": len(paper_graph.edges),
            })

        logger.info("Final graph: %d nodes, %d edges", len(paper_graph.nodes), len(paper_graph.edges))
        return graph_data

    # ── Fallback: chunk-based extraction (no PaperGraph) ──
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
