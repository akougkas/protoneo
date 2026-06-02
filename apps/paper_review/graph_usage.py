"""Paper-review-specific graph usage metrics."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from protoneo.knowledge.graph import GraphNode, KnowledgeGraph

_STRUCTURAL_NODE_TYPES = {"Paper", "Section", "Diagram", "Figure", "Table", "Reference", "Equation"}
_EVIDENCE_NODE_TYPES = {"Figure", "Table", "Equation"}
_EVALUATION_NODE_TYPES = {"Result", "Metric", "Baseline", "Workload", "Dataset"}


def compute_review_graph_utilization(
    graph: KnowledgeGraph,
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    """Measure explicit graph-entity mentions in paper-review outputs.

    This is intentionally deterministic and conservative. It measures whether
    reviewers named graph entities or aliases; it does not prove that a reviewer
    used the graph, and it misses paraphrases and manuscript-derived uses of the
    same facts.
    """
    semantic = [
        node for node in graph.nodes
        if node.node_type not in _STRUCTURAL_NODE_TYPES and len(node.label.strip()) > 3
    ]
    if not semantic:
        return {
            "per_entity": [],
            "per_reviewer": {},
            "unreferenced_entities": [],
            "utilization_ratio": 0.0,
            "overall_ratio": 0.0,
            "by_type": {},
            "method": "paper_review_alias_boundary_match_v1",
            "limitations": _limitations(),
        }

    reviewer_texts: dict[str, str] = {}
    for review in reviews:
        aid = str(review.get("agent_id") or review.get("reviewer_role") or "unknown")
        reviewer_texts[aid] = _extract_review_text(review)

    per_entity: list[dict[str, Any]] = []
    referenced_ids: set[str] = set()
    type_coverage: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "referenced": 0})

    for node in semantic:
        aliases = _match_aliases(node)
        mentioning_reviewers = []
        for aid, text in reviewer_texts.items():
            if any(_contains_alias(text, alias) for alias in aliases):
                mentioning_reviewers.append(aid)
        per_entity.append({
            "entity_id": node.id,
            "label": node.label,
            "type": node.node_type,
            "aliases": aliases,
            "mentioned_by": mentioning_reviewers,
            "mention_count": len(mentioning_reviewers),
        })
        type_coverage[node.node_type]["total"] += 1
        if mentioning_reviewers:
            referenced_ids.add(node.id)
            type_coverage[node.node_type]["referenced"] += 1

    unreferenced = [
        {"entity_id": item["entity_id"], "label": item["label"], "type": item["type"]}
        for item in per_entity
        if item["mention_count"] == 0
    ]

    per_reviewer: dict[str, dict[str, Any]] = {}
    for aid in reviewer_texts:
        entities_covered = sum(1 for item in per_entity if aid in item["mentioned_by"])
        per_reviewer[aid] = {
            "entities_covered": entities_covered,
            "total_entities": len(semantic),
            "coverage_ratio": round(entities_covered / max(len(semantic), 1), 3),
        }

    by_type: dict[str, dict[str, Any]] = {}
    for etype, counts in type_coverage.items():
        by_type[etype] = {
            "total": counts["total"],
            "referenced": counts["referenced"],
            "ratio": round(counts["referenced"] / max(counts["total"], 1), 3),
        }

    ratio = round(len(referenced_ids) / max(len(semantic), 1), 3)
    return {
        "per_entity": per_entity,
        "per_reviewer": per_reviewer,
        "unreferenced_entities": unreferenced,
        "utilization_ratio": ratio,
        "overall_ratio": ratio,
        "by_type": by_type,
        "method": "paper_review_alias_boundary_match_v1",
        "limitations": _limitations(),
    }


def compute_review_graph_value_metrics(
    graph: KnowledgeGraph,
    reviews: list[dict[str, Any]],
    *,
    ablation_reviews: dict[str, list[dict[str, Any]]] | None = None,
    prompt_token_estimates: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Compute deterministic graph-value metrics for review outputs.

    These metrics are conservative audit signals. They are designed to make
    ablation results comparable without calling another model. They do not
    prove causal graph use unless the ablation labels and execution provenance
    show that the compared reviews were generated under those contexts.
    """
    utilization = compute_review_graph_utilization(graph, reviews)
    all_review_text = " ".join(_extract_review_text(review) for review in reviews)
    evidence_nodes = [
        node for node in graph.nodes
        if node.node_type in _EVIDENCE_NODE_TYPES
    ]
    evaluation_nodes = [
        node for node in graph.nodes
        if node.node_type in _EVALUATION_NODE_TYPES
    ]
    useful_grounded_facts = _count_grounded_facts(reviews)

    metrics: dict[str, Any] = {
        "explicit_entity_utilization": utilization,
        "figure_table_equation_coverage": _node_coverage(evidence_nodes, all_review_text),
        "result_metric_baseline_workload_coverage": _node_coverage(evaluation_nodes, all_review_text),
        "evidence_citation_precision_recall": _citation_metrics(reviews),
        "unsupported_claim_rate": _unsupported_claim_rate(reviews),
        "graph_only_unique_facts_used": [],
        "markdown_only_unique_facts_used": [],
        "reviewer_blind_spot_reduction": {},
        "specificity_factuality_delta": {},
        "token_cost_per_useful_grounded_fact": _token_cost(
            prompt_token_estimates or {},
            useful_grounded_facts,
        ),
        "method": "paper_review_deterministic_grounding_audit_v1",
        "limitations": [
            "Requires comparable ablation review sets to estimate deltas.",
            "Uses exact label, alias, citation, and evidence-field matches only.",
            "Does not judge whether a reviewer interpretation is correct.",
            "Does not prove graph causality when graph facts also appear in markdown.",
            "Unsupported-claim rate only counts empty evidence fields in structured outputs.",
        ],
    }

    if ablation_reviews:
        metrics.update(_ablation_metrics(graph, reviews, ablation_reviews))
    return metrics


def _extract_review_text(review: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "summary",
        "comments_for_authors",
        "comments_for_pc",
        "internal_committee_concerns",
        "raw_content",
    ):
        value = review.get(key, "")
        if value:
            parts.append(_stringify(value))
    for key in (
        "strengths",
        "weaknesses",
        "questions_for_authors",
        "revision_actions",
        "citations",
    ):
        for item in review.get(key, []) or []:
            parts.append(_stringify(item))
    return " ".join(parts).lower()


def _node_coverage(nodes: list[GraphNode], text: str) -> dict[str, Any]:
    covered = []
    missing = []
    for node in nodes:
        aliases = _match_aliases(node)
        item = {
            "entity_id": node.id,
            "label": node.label,
            "type": node.node_type,
        }
        if any(_contains_alias(text, alias) for alias in aliases):
            covered.append(item)
        else:
            missing.append(item)
    total = len(nodes)
    return {
        "covered": covered,
        "missing": missing,
        "covered_count": len(covered),
        "total": total,
        "coverage_ratio": round(len(covered) / max(total, 1), 3),
    }


def _citation_metrics(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    citation_count = 0
    supported_citations = 0
    graph_citations = 0
    manuscript_citations = 0
    substantive_items = 0
    items_with_citation = 0

    for review in reviews:
        citations = review.get("citations") or []
        if isinstance(citations, list):
            for citation in citations:
                if not isinstance(citation, dict):
                    continue
                citation_count += 1
                has_manuscript_ref = any(
                    str(citation.get(key) or "").strip()
                    for key in ("section", "figure", "table", "page")
                )
                has_graph_ref = bool(str(citation.get("graph_ref") or "").strip())
                if has_manuscript_ref or has_graph_ref:
                    supported_citations += 1
                if has_graph_ref:
                    graph_citations += 1
                if has_manuscript_ref:
                    manuscript_citations += 1

        for key in ("strengths", "weaknesses", "revision_actions", "questions_for_authors"):
            for item in review.get(key, []) or []:
                substantive_items += 1
                text = _stringify(item).lower()
                has_inline_evidence = bool(_evidence_text(item))
                if has_inline_evidence or any(text and text in _stringify(c).lower() for c in citations):
                    items_with_citation += 1

    return {
        "citation_count": citation_count,
        "supported_citation_count": supported_citations,
        "manuscript_citation_count": manuscript_citations,
        "graph_citation_count": graph_citations,
        "precision": round(supported_citations / max(citation_count, 1), 3),
        "substantive_item_count": substantive_items,
        "substantive_items_with_evidence": items_with_citation,
        "recall_proxy": round(items_with_citation / max(substantive_items, 1), 3),
        "recall_proxy_definition": "fraction of structured substantive items with an evidence field or matching citation text",
    }


def _unsupported_claim_rate(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    unsupported = []
    total = 0
    for review in reviews:
        reviewer = str(review.get("agent_id") or review.get("reviewer_role") or "unknown")
        for key in ("strengths", "weaknesses", "revision_actions"):
            for item in review.get(key, []) or []:
                text = _stringify(item)
                if not text.strip():
                    continue
                total += 1
                if not _evidence_text(item):
                    unsupported.append({
                        "reviewer": reviewer,
                        "field": key,
                        "claim": text[:240],
                    })
    return {
        "unsupported_count": len(unsupported),
        "total_claim_like_items": total,
        "rate": round(len(unsupported) / max(total, 1), 3),
        "examples": unsupported[:12],
    }


def _count_grounded_facts(reviews: list[dict[str, Any]]) -> int:
    count = 0
    for review in reviews:
        for key in ("strengths", "weaknesses", "revision_actions"):
            for item in review.get(key, []) or []:
                if _evidence_text(item):
                    count += 1
        for citation in review.get("citations", []) or []:
            if isinstance(citation, dict) and any(
                str(citation.get(key) or "").strip()
                for key in ("section", "figure", "table", "page", "graph_ref")
            ):
                count += 1
    return count


def _token_cost(prompt_token_estimates: dict[str, int], useful_grounded_facts: int) -> dict[str, Any]:
    total_tokens = sum(
        int(value)
        for value in prompt_token_estimates.values()
        if isinstance(value, (int, float))
    )
    return {
        "prompt_tokens": total_tokens,
        "useful_grounded_fact_count": useful_grounded_facts,
        "tokens_per_useful_grounded_fact": (
            round(total_tokens / useful_grounded_facts, 2)
            if useful_grounded_facts else None
        ),
    }


def _ablation_metrics(
    graph: KnowledgeGraph,
    target_reviews: list[dict[str, Any]],
    ablation_reviews: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    target_facts = _fact_set(target_reviews)
    by_mode = {
        mode: _fact_set(reviews)
        for mode, reviews in ablation_reviews.items()
    }
    graph_only = by_mode.get("graph_only", set())
    markdown_only = by_mode.get("markdown_only", set())

    markdown_blind_spots = sorted(target_facts - markdown_only)
    graph_blind_spots = sorted(target_facts - graph_only)
    target_citations = _citation_metrics(target_reviews)
    markdown_citations = _citation_metrics(ablation_reviews.get("markdown_only", []))
    target_unsupported = _unsupported_claim_rate(target_reviews)
    markdown_unsupported = _unsupported_claim_rate(ablation_reviews.get("markdown_only", []))

    return {
        "graph_only_unique_facts_used": sorted(graph_only - markdown_only)[:50],
        "markdown_only_unique_facts_used": sorted(markdown_only - graph_only)[:50],
        "reviewer_blind_spot_reduction": {
            "target_minus_markdown_only_count": len(markdown_blind_spots),
            "target_minus_markdown_only_examples": markdown_blind_spots[:25],
            "target_minus_graph_only_count": len(graph_blind_spots),
            "target_minus_graph_only_examples": graph_blind_spots[:25],
        },
        "specificity_factuality_delta": {
            "citation_recall_proxy_delta_vs_markdown_only": round(
                target_citations["recall_proxy"] - markdown_citations["recall_proxy"], 3
            ),
            "unsupported_claim_rate_delta_vs_markdown_only": round(
                target_unsupported["rate"] - markdown_unsupported["rate"], 3
            ),
            "target_entity_utilization": compute_review_graph_utilization(graph, target_reviews).get("utilization_ratio", 0.0),
            "markdown_only_entity_utilization": compute_review_graph_utilization(
                graph, ablation_reviews.get("markdown_only", [])
            ).get("utilization_ratio", 0.0),
        },
    }


def _fact_set(reviews: list[dict[str, Any]]) -> set[str]:
    facts: set[str] = set()
    for review in reviews:
        for key in ("strengths", "weaknesses", "revision_actions", "citations"):
            for item in review.get(key, []) or []:
                text = _stringify(item).lower()
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) >= 24:
                    facts.add(text[:240])
    return facts


def _evidence_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in (
            "evidence",
            "section",
            "page",
            "figure",
            "table",
            "graph_ref",
            "why_it_matters",
            "target_section",
        ):
            value = item.get(key)
            if value:
                return _stringify(value)
    return ""


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        fields = []
        for key in ("text", "point", "description", "evidence", "claim", "graph_ref", "section", "page"):
            if value.get(key):
                fields.append(str(value[key]))
        return " ".join(fields) if fields else " ".join(str(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_stringify(item) for item in value)
    return str(value)


def _match_aliases(node: GraphNode) -> list[str]:
    aliases: set[str] = set()
    label = node.label.strip()
    if len(label) >= 4:
        aliases.add(label.lower())
    for sep in (":", "(", " - ", " — "):
        if sep in label:
            short = label.split(sep)[0].strip()
            if len(short) >= 4:
                aliases.add(short.lower())
            break
    short_name = node.attributes.get("short_name") if isinstance(node.attributes, dict) else ""
    if isinstance(short_name, str) and len(short_name.strip()) >= 3:
        aliases.add(short_name.strip().lower())
    return sorted(aliases, key=lambda value: (-len(value), value))


def _contains_alias(text: str, alias: str) -> bool:
    if not alias:
        return False
    if alias.isalnum() or re.match(r"^[\w .+-]+$", alias):
        pattern = r"(?<!\w)" + re.escape(alias) + r"(?!\w)"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None
    return alias in text


def _limitations() -> list[str]:
    return [
        "Counts explicit label or alias mentions only.",
        "Does not detect semantic paraphrases.",
        "Does not prove graph causality when the same fact appears in manuscript markdown.",
        "Can still overcount generic aliases that are valid terms in ordinary prose.",
        "Does not measure whether cited graph facts were correct or useful.",
    ]
