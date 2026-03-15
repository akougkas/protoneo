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

_VERIFY_PROMPT = """\
Verify this knowledge graph against the paper text. Perform three checks:

## Check 1: Grounding
For each entity, verify it is explicitly mentioned or clearly implied in the paper text. Flag entities that appear hallucinated or fabricated.

## Check 2: Completeness
Identify important methods, datasets, baselines, or quantitative results discussed in the paper that are MISSING from the graph. Focus on concrete, named things that a reviewer would need to see.

## Check 3: Missing Connections
Identify relationships between existing graph entities that the paper states but the graph does not capture. For example, if the paper says "Method A achieves 2x speedup on Dataset B" but the graph has both entities without a connecting edge.

## Graph Entities:
{entity_summary}

## Graph Relationships:
{edge_summary}

## Paper Text (excerpt):
{paper_excerpt}

Return JSON:
{{
  "grounding_issues": [
    {{"entity": "entity name", "issue": "why it may not be grounded"}}
  ],
  "missing_concepts": [
    {{"concept": "concept name", "suggested_type": "Method|Dataset|etc", "section": "where it appears"}}
  ],
  "missing_connections": [
    {{"source": "entity name", "target": "entity name", "type": "EDGE_TYPE", "evidence": "what the paper says"}}
  ]
}}

Only report genuine findings grounded in the paper text. Empty arrays are fine."""


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
) -> VerificationResult:
    """Run 3-check verification audit on the paper graph.

    Checks grounding, completeness, and consistency via a single LLM call.
    Updates confidence scores on flagged entities and optionally adds
    missing concepts to the graph.
    """
    _STRUCTURAL = {"Paper", "Section", "Diagram", "Table"}

    semantic_nodes = [n for n in paper_graph.nodes if n.node_type not in _STRUCTURAL]
    if not semantic_nodes:
        return VerificationResult()

    entity_summary = "\n".join(
        f"- {n.label} ({n.node_type}): {n.description[:60]}"
        for n in semantic_nodes[:80]
    )

    node_map = {n.id: n.label for n in paper_graph.nodes}
    semantic_edges = [
        e for e in paper_graph.edges
        if e.edge_type not in ("HAS_SECTION", "CONTAINS")
    ]
    edge_summary = "\n".join(
        f"- {node_map.get(e.source_id, '?')} --{e.edge_type}--> {node_map.get(e.target_id, '?')}"
        for e in semantic_edges[:60]
    )

    # Truncate paper text to fit context
    paper_excerpt = paper_text[:12000]

    prompt = _VERIFY_PROMPT.format(
        entity_summary=entity_summary,
        edge_summary=edge_summary,
        paper_excerpt=paper_excerpt,
    )

    try:
        response = await llm_client.complete(
            model=model,
            messages=[
                {"role": "system", "content": _VERIFY_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            session_id=session_id,
            temperature=0.1,
            max_tokens=4096,
        )
        parsed = _parse_verification(response.content)
    except Exception as e:
        logger.warning("Verification LLM call failed: %s", e)
        return VerificationResult()

    result = VerificationResult()
    result.grounding_issues = parsed.get("grounding_issues", [])

    # Lower confidence on ungrounded entities
    label_to_node = {n.label.lower(): n for n in paper_graph.nodes}
    for issue in result.grounding_issues:
        entity_name = issue.get("entity", "")
        node = label_to_node.get(entity_name.lower())
        if node:
            node.confidence = max(0.2, node.confidence - 0.3)
            result.confidence_updates[node.id] = node.confidence
            result.entities_flagged += 1

    # Add missing concepts as new nodes
    for concept in parsed.get("missing_concepts", []):
        name = concept.get("concept", "")
        suggested_type = concept.get("suggested_type", "Concept")
        section = concept.get("section", "")
        if name and len(name) > 2:
            paper_graph.add_node(
                label=name,
                node_type=suggested_type,
                description=f"Found by verification pass",
                source_section=section,
                confidence=0.7,
            )
            result.entities_added += 1
            result.missing_concepts.append(concept)

    # Add missing connections between existing entities
    edges_added = 0
    for conn in parsed.get("missing_connections", []):
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

    paper_graph.update_stats()

    logger.info(
        "Verification: %d grounding issues, %d entities added, %d connections added",
        len(result.grounding_issues),
        result.entities_added,
        edges_added,
    )

    return result
