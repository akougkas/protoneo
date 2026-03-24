"""Co-reference resolution and abbreviation linking.

Runs after all sections have been extracted. Identifies entities that
refer to the same concept (co-references) and links abbreviations
to their full forms with ALIAS_OF edges.

Does NOT merge abbreviations into full names. Both forms are preserved
as separate nodes connected by ALIAS_OF.
"""

import json
import logging
import re
from typing import Any

from ..llm.client import LLMClient
from .paper_graph import PaperGraph

logger = logging.getLogger("protoneo.knowledge.coref_resolver")

_COREF_SYSTEM = "You resolve co-references and identify abbreviations in knowledge graph entities extracted from an academic paper. Always respond with valid JSON only."

_COREF_PROMPT = """\
Below are entities extracted from an academic paper. Identify:

1. **Co-references**: entities that refer to the same concept and should be merged.
   Keep the most descriptive name. List names to remove.
2. **Abbreviations**: short forms that are aliases for longer entity names.
   These should NOT be merged. Both forms are kept, linked by ALIAS_OF.

## Entities:
{entity_list}

Return JSON:
{{
  "merges": [
    {{"keep": "full descriptive name", "remove": ["synonym1", "synonym2"]}}
  ],
  "aliases": [
    {{"full": "full name", "abbreviation": "short form"}}
  ]
}}

Only include confident matches. Do not guess. If no merges or aliases exist, return empty arrays."""

_COREF_PAIRS_PROMPT = """\
These entity pairs from an academic paper knowledge graph may be duplicates or abbreviations. For each pair, decide:
- MERGE: they refer to the same concept (keep the more descriptive name)
- ALIAS: one is an abbreviation of the other (link with ALIAS_OF, keep both)
- DISTINCT: they are different concepts (no action)

## Candidate Pairs:
{pair_list}

Return JSON:
{{
  "merges": [
    {{"keep": "descriptive name", "remove": ["duplicate name"]}}
  ],
  "aliases": [
    {{"full": "full name", "abbreviation": "short form"}}
  ]
}}

Only include MERGE and ALIAS decisions. Omit DISTINCT pairs."""


def _parse_coref_response(raw: str) -> dict:
    """Parse the LLM co-reference resolution response."""
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

    logger.warning("Failed to parse coref response")
    return {"merges": [], "aliases": []}


def _is_acronym_match(short: str, long: str) -> bool:
    """Check if short (2-5 all-caps chars) could be an acronym for long."""
    if not (2 <= len(short) <= 5 and short.isupper()):
        return False
    words = long.split()
    if len(words) < 2:
        return False
    initials = "".join(w[0].upper() for w in words if w and w[0].isalpha())
    return initials == short or initials.startswith(short)


def _split_camel(label: str) -> set[str]:
    """Tokenize a label by splitting on spaces, underscores, and CamelCase boundaries.

    'StandardBaseline' -> {'standard', 'baseline'}
    'HAR dataset'      -> {'har', 'dataset'}
    'HARDataset'       -> {'har', 'dataset'}
    'WISDM_Dataset'    -> {'wisdm', 'dataset'}
    """
    import re as _re
    # Replace underscores with spaces
    s = label.replace("_", " ")
    # Split CamelCase
    s = _re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', s)
    s = _re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', s)
    return {w.lower() for w in s.split() if w}


def _find_candidate_pairs(entities: list) -> list[tuple[str, str]]:
    """Fast pre-pass: find entity pairs with high token overlap or acronym matches.

    Uses CamelCase-aware tokenization so 'StandardBaseline' and 'Standard Baseline'
    produce the same token set {'standard', 'baseline'}.
    """
    pairs = []
    seen = set()
    names = [(n.label, _split_camel(n.label)) for n in entities]

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            label_i, tokens_i = names[i]
            label_j, tokens_j = names[j]
            if not tokens_i or not tokens_j:
                continue

            pair_key = (min(label_i, label_j), max(label_i, label_j))
            if pair_key in seen:
                continue

            overlap = len(tokens_i & tokens_j)
            union = len(tokens_i | tokens_j)
            jaccard = overlap / union if union else 0
            li, lj = label_i.lower(), label_j.lower()
            is_substring = li in lj or lj in li
            is_acronym = _is_acronym_match(label_i, label_j) or _is_acronym_match(label_j, label_i)

            if jaccard > 0.3 or is_substring or is_acronym:
                pairs.append((label_i, label_j))
                seen.add(pair_key)

    return pairs


async def resolve_coreferences(
    paper_graph: PaperGraph,
    llm_client: LLMClient,
    model: str = "",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Resolve co-references and link abbreviations in the paper graph.

    Returns stats dict with merge/alias counts.
    """
    _STRUCTURAL = {"Paper", "Section", "Diagram", "Table", "Reference", "Equation"}
    entities = [
        n for n in paper_graph.nodes if n.node_type not in _STRUCTURAL
    ]

    if len(entities) < 3:
        return {"merged": 0, "aliases_created": 0,
                "total_entities_before": len(entities),
                "total_entities_after": len(entities)}

    candidate_pairs = _find_candidate_pairs(entities)

    # Multi-pass: process candidate pairs in chunks of 40
    all_results: list[dict] = []

    if candidate_pairs:
        pair_chunks = [candidate_pairs[i:i+40] for i in range(0, len(candidate_pairs), 40)]
        logger.info(
            "Co-ref pre-pass found %d candidate pairs from %d entities (%d chunks)",
            len(candidate_pairs), len(entities), len(pair_chunks),
        )

        for chunk_idx, pair_chunk in enumerate(pair_chunks):
            pair_list = "\n".join(
                f"- \"{a}\" vs \"{b}\"" for a, b in pair_chunk
            )
            prompt = _COREF_PAIRS_PROMPT.format(pair_list=pair_list)
            try:
                response = await llm_client.complete(
                    model=model,
                    messages=[
                        {"role": "system", "content": _COREF_SYSTEM},
                        {"role": "user", "content": prompt},
                    ],
                    session_id=session_id,
                    temperature=0.1,
                    max_tokens=4096,
                )
                all_results.append(_parse_coref_response(response.content))
                logger.info("Coref pairs chunk %d/%d complete", chunk_idx + 1, len(pair_chunks))
            except Exception as e:
                logger.warning("Co-ref pairs chunk %d failed: %s", chunk_idx + 1, e)

    # Second pass: send full entity list for abbreviation detection
    entity_list = "\n".join(
        f"- {n.label} ({n.node_type}): {n.description[:80]}"
        for n in entities
    )
    abbrev_prompt = _COREF_PROMPT.format(entity_list=entity_list)
    logger.info(
        "Abbreviation detection pass: sending all %d entities", len(entities),
    )
    try:
        response = await llm_client.complete(
            model=model,
            messages=[
                {"role": "system", "content": _COREF_SYSTEM},
                {"role": "user", "content": abbrev_prompt},
            ],
            session_id=session_id,
            temperature=0.1,
            max_tokens=4096,
        )
        all_results.append(_parse_coref_response(response.content))
    except Exception as e:
        logger.warning("Abbreviation detection LLM call failed: %s", e)

    if not all_results:
        return {"merged": 0, "aliases_created": 0,
                "total_entities_before": len(entities),
                "total_entities_after": len(entities)}

    # Merge all results, deduplicating
    result: dict = {"merges": [], "aliases": []}
    seen_merges: set[str] = set()
    seen_aliases: set[tuple[str, str]] = set()
    for r in all_results:
        for m in r.get("merges", []):
            key = m.get("keep", "").lower()
            if key and key not in seen_merges:
                result["merges"].append(m)
                seen_merges.add(key)
        for a in r.get("aliases", []):
            key = (a.get("full", "").lower(), a.get("abbreviation", "").lower())
            if key[0] and key[1] and key not in seen_aliases:
                result["aliases"].append(a)
                seen_aliases.add(key)

    total_before = len(paper_graph.nodes)
    merged_count = 0
    alias_count = 0

    # Process merges
    label_to_node = {n.label.lower(): n for n in paper_graph.nodes}
    for merge in result.get("merges", []):
        keep_label = merge.get("keep", "")
        keep_node = label_to_node.get(keep_label.lower())
        if not keep_node:
            continue

        for remove_label in merge.get("remove", []):
            remove_node = label_to_node.get(remove_label.lower())
            if not remove_node or remove_node.id == keep_node.id:
                continue

            # Redirect edges from remove_node to keep_node
            for edge in paper_graph.edges:
                if edge.source_id == remove_node.id:
                    edge.source_id = keep_node.id
                if edge.target_id == remove_node.id:
                    edge.target_id = keep_node.id

            # Remove the duplicate node
            paper_graph.nodes = [
                n for n in paper_graph.nodes if n.id != remove_node.id
            ]
            label_to_node.pop(remove_label.lower(), None)
            merged_count += 1
            logger.info("Merged '%s' into '%s'", remove_label, keep_label)

    # Fix 10: Deduplicate edges and remove self-loops after merges
    seen_edges = set()
    deduped_edges = []
    self_loops_removed = 0
    for e in paper_graph.edges:
        if e.source_id == e.target_id:
            self_loops_removed += 1
            continue
        key = (e.source_id, e.target_id, e.edge_type)
        if key not in seen_edges:
            deduped_edges.append(e)
            seen_edges.add(key)
    paper_graph.edges = deduped_edges
    if self_loops_removed:
        logger.info("Removed %d self-loop edges after co-ref merge", self_loops_removed)

    # Process aliases (create ALIAS_OF edges, do NOT merge)
    label_to_node = {n.label.lower(): n for n in paper_graph.nodes}
    for alias in result.get("aliases", []):
        full_name = alias.get("full", "")
        abbrev = alias.get("abbreviation", "")
        full_node = label_to_node.get(full_name.lower())
        abbrev_node = label_to_node.get(abbrev.lower())

        if full_node and abbrev_node and full_node.id != abbrev_node.id:
            paper_graph.add_edge(
                source_id=abbrev_node.id,
                target_id=full_node.id,
                edge_type="ALIAS_OF",
                description=f"{abbrev} is abbreviation for {full_name}",
            )
            alias_count += 1
            logger.info("Created ALIAS_OF: '%s' -> '%s'", abbrev, full_name)

    paper_graph.update_stats()

    return {
        "merged": merged_count,
        "aliases_created": alias_count,
        "total_entities_before": total_before,
        "total_entities_after": len(paper_graph.nodes),
    }
