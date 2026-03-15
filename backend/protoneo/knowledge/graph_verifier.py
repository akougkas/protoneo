"""Graph verification audit.

Three-check audit after extraction and co-reference resolution:
1. Grounding: is each entity actually mentioned in the paper text?
2. Completeness: what concepts does the paper discuss that have no node?
3. Consistency: do any edges contradict each other?

Output: confidence scores per node/edge, list of issues.
"""

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from ..llm.client import LLMClient
from .paper_graph import PaperGraph

logger = logging.getLogger("protoneo.knowledge.graph_verifier")


class VerificationResult(BaseModel):
    """Results of the verification audit."""

    grounding_issues: list[dict] = Field(default_factory=list)
    missing_concepts: list[dict] = Field(default_factory=list)
    missing_connections: list[dict] = Field(default_factory=list)
    confidence_updates: dict[str, float] = Field(default_factory=dict)
    entities_added: int = 0
    entities_flagged: int = 0


_VERIFY_SYSTEM = "You verify knowledge graphs extracted from academic papers. Always respond with valid JSON only."

# ── Pass 1: Connectivity ──
_VERIFY_CONNECTIVITY_PROMPT = """\
Analyze the connectivity of this knowledge graph extracted from an academic paper.

## Graph Entities (with types):
{entity_summary}

## Graph Relationships:
{edge_summary}

## Paper Sections:
{section_list}

Identify:
1. **Disconnected entities**: semantic entities that have no edges connecting them to the rest of the graph. For each, suggest which existing entity they should connect to and what edge type to use.
2. **Missing APPEARS_IN edges**: entities that clearly belong to a specific section but lack an APPEARS_IN edge to that section node. Check if the entity's source_section matches a section node.
3. **Isolated subgraphs**: groups of entities that connect to each other but not to the main graph. Suggest bridge edges.

Return JSON:
{{
  "missing_connections": [
    {{"source": "entity name", "target": "entity or section name", "type": "EDGE_TYPE", "evidence": "why this connection should exist"}}
  ]
}}

Only propose connections grounded in the paper's content. Empty array is fine."""

# ── Pass 2: Completeness ──
_VERIFY_COMPLETENESS_PROMPT = """\
Check this knowledge graph for completeness against the full paper text.

## Current Graph Entities:
{entity_summary}

## Paper Text:
{paper_text}

Identify concrete, named entities discussed in the paper that are MISSING from the graph. Focus on:
- Methods, algorithms, or techniques mentioned by name
- Datasets or benchmarks used for evaluation
- Baselines or competing systems compared against
- Specific quantitative results (numbers, metrics, performance values)
- Hardware platforms, systems, or tools used

Do NOT suggest vague concepts. Every suggestion must be a specific named thing from the paper.

Return JSON:
{{
  "missing_concepts": [
    {{"concept": "exact name from paper", "suggested_type": "Method|Dataset|Baseline|Result|etc", "section": "which section mentions it", "evidence": "quote or paraphrase from paper"}}
  ]
}}"""

# ── Pass 3: Grounding ──
_VERIFY_GROUNDING_PROMPT = """\
Verify that each entity in this knowledge graph is grounded in the paper text.

## Graph Entities with Descriptions:
{entity_details}

## Paper Text:
{paper_text}

For each entity, check:
1. Is the entity name explicitly mentioned or clearly implied in the paper?
2. Is the description accurate to what the paper says?
3. Could this entity be a hallucination (not actually in the paper)?

Return JSON:
{{
  "grounding_issues": [
    {{"entity": "entity name", "issue": "why it may not be grounded", "confidence": 0.0}}
  ]
}}

Only flag entities you are confident are NOT in the paper. Include a confidence score (0.0 to 1.0) for the entity's grounding. Empty array is fine."""


def _parse_verification(raw: str) -> dict:
    """Parse the LLM verification response."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass

    fence = re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    start = raw.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start : i + 1])
                    except (json.JSONDecodeError, TypeError):
                        break

    logger.warning("Failed to parse verification response")
    return {"grounding_issues": [], "missing_concepts": [], "missing_connections": []}


async def verify_graph(
    paper_graph: PaperGraph,
    paper_text: str,
    llm_client: LLMClient,
    model: str = "",
    session_id: str | None = None,
    markdown: str = "",
) -> VerificationResult:
    """Run 3-pass verification audit on the paper graph.

    Pass 1 (Connectivity): Identifies disconnected entities and missing
        APPEARS_IN edges. Directly fixes the structural-semantic gap.
    Pass 2 (Completeness): Finds concrete named entities missing from
        the graph by scanning the full paper text.
    Pass 3 (Grounding): Flags hallucinated entities and lowers their
        confidence scores.

    All passes use the full graph and full paper text (no truncation).
    """
    _STRUCTURAL = {"Paper", "Section", "Diagram", "Table"}

    semantic_nodes = [n for n in paper_graph.nodes if n.node_type not in _STRUCTURAL]
    if not semantic_nodes:
        return VerificationResult()

    result = VerificationResult()
    node_map = {n.id: n.label for n in paper_graph.nodes}
    label_to_node = {n.label.lower(): n for n in paper_graph.nodes}

    # Full entity summary (no cap)
    entity_summary = "\n".join(
        f"- {n.label} ({n.node_type}): {n.description[:60]}"
        for n in semantic_nodes
    )

    # Full edge summary (no cap)
    all_edges = paper_graph.edges
    edge_summary = "\n".join(
        f"- {node_map.get(e.source_id, '?')} --{e.edge_type}--> {node_map.get(e.target_id, '?')}"
        for e in all_edges
    )

    # Section list for connectivity pass
    section_list = "\n".join(
        f"- {n.label}" for n in paper_graph.nodes if n.node_type == "Section"
    )

    # Prefer markdown for paper text, fall back to flat text
    full_text = markdown if markdown else paper_text

    edges_added = 0

    # ── Pass 1: Connectivity ──
    try:
        prompt1 = _VERIFY_CONNECTIVITY_PROMPT.format(
            entity_summary=entity_summary,
            edge_summary=edge_summary,
            section_list=section_list,
        )
        response1 = await llm_client.complete(
            model=model,
            messages=[
                {"role": "system", "content": _VERIFY_SYSTEM},
                {"role": "user", "content": prompt1},
            ],
            session_id=session_id,
            temperature=0.1,
            max_tokens=8192,
        )
        parsed1 = _parse_verification(response1.content)

        for conn in parsed1.get("missing_connections", []):
            src_name = conn.get("source", "")
            tgt_name = conn.get("target", "")
            edge_type = conn.get("type", "RELATED_TO")
            evidence = conn.get("evidence", "")
            src_node = label_to_node.get(src_name.lower())
            tgt_node = label_to_node.get(tgt_name.lower())
            if src_node and tgt_node and src_node.id != tgt_node.id:
                paper_graph.add_edge(
                    source_id=src_node.id,
                    target_id=tgt_node.id,
                    edge_type=edge_type,
                    description=evidence,
                )
                edges_added += 1
                result.missing_connections.append(conn)

        logger.info("Verification pass 1 (connectivity): %d edges added", edges_added)
    except Exception as e:
        logger.warning("Verification pass 1 (connectivity) failed: %s", e)

    # ── Pass 2: Completeness ──
    try:
        # Refresh entity summary after pass 1 additions
        entity_summary_2 = "\n".join(
            f"- {n.label} ({n.node_type})"
            for n in paper_graph.nodes if n.node_type not in _STRUCTURAL
        )
        prompt2 = _VERIFY_COMPLETENESS_PROMPT.format(
            entity_summary=entity_summary_2,
            paper_text=full_text,
        )
        response2 = await llm_client.complete(
            model=model,
            messages=[
                {"role": "system", "content": _VERIFY_SYSTEM},
                {"role": "user", "content": prompt2},
            ],
            session_id=session_id,
            temperature=0.1,
            max_tokens=8192,
        )
        parsed2 = _parse_verification(response2.content)

        label_to_node = {n.label.lower(): n for n in paper_graph.nodes}
        for concept in parsed2.get("missing_concepts", []):
            name = concept.get("concept", "")
            suggested_type = concept.get("suggested_type", "Concept")
            section = concept.get("section", "")
            if name and len(name) > 2 and name.lower() not in label_to_node:
                paper_graph.add_node(
                    label=name,
                    node_type=suggested_type,
                    description=f"Found by verification completeness pass",
                    source_section=section,
                    confidence=0.7,
                )
                result.entities_added += 1
                result.missing_concepts.append(concept)

        logger.info("Verification pass 2 (completeness): %d entities added", result.entities_added)
    except Exception as e:
        logger.warning("Verification pass 2 (completeness) failed: %s", e)

    # ── Pass 3: Grounding ──
    try:
        entity_details = "\n".join(
            f"- {n.label} ({n.node_type}): {n.description}"
            for n in paper_graph.nodes if n.node_type not in _STRUCTURAL
        )
        prompt3 = _VERIFY_GROUNDING_PROMPT.format(
            entity_details=entity_details,
            paper_text=full_text,
        )
        response3 = await llm_client.complete(
            model=model,
            messages=[
                {"role": "system", "content": _VERIFY_SYSTEM},
                {"role": "user", "content": prompt3},
            ],
            session_id=session_id,
            temperature=0.1,
            max_tokens=8192,
        )
        parsed3 = _parse_verification(response3.content)

        result.grounding_issues = parsed3.get("grounding_issues", [])
        label_to_node = {n.label.lower(): n for n in paper_graph.nodes}
        for issue in result.grounding_issues:
            entity_name = issue.get("entity", "")
            llm_conf = issue.get("confidence", 0.3)
            node = label_to_node.get(entity_name.lower())
            if node:
                # Set confidence to the LLM's grounding score
                node.confidence = max(0.1, min(float(llm_conf), node.confidence))
                result.confidence_updates[node.id] = node.confidence
                result.entities_flagged += 1

        logger.info("Verification pass 3 (grounding): %d entities flagged", result.entities_flagged)
    except Exception as e:
        logger.warning("Verification pass 3 (grounding) failed: %s", e)

    paper_graph.update_stats()

    logger.info(
        "Verification complete: %d grounding issues, %d entities added, %d connections added",
        len(result.grounding_issues),
        result.entities_added,
        edges_added,
    )

    return result
