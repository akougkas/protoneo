"""Deterministic local knowledge-graph querying.

Reviewers and the post-review PC Chair often need to answer concrete,
graph-grounded questions: which claims lack supporting results, which methods
are connected to datasets/metrics, which baselines a method is compared
against, which figures/tables back a claim, and how entities are distributed
across sections.

This module answers those questions deterministically from a KnowledgeGraph.
It is kernel-owned and has no application dependency. No network calls, no LLM
calls: given the same graph it always returns the same answer, so it is safe to
expose as a tool and to unit test.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..knowledge.graph import GraphEdge, GraphNode, KnowledgeGraph
from .types import ToolResult

# Mirror the structural/semantic split used elsewhere in the kernel so the
# query results align with the rendered graph briefings reviewers already see.
STRUCTURAL_NODE_TYPES = {
    "Paper", "Section", "Diagram", "Figure", "Table", "Reference", "Equation",
}
STRUCTURAL_EDGE_TYPES = {"HAS_SECTION", "CONTAINS", "HAS_ARTIFACT", "APPEARS_IN"}

# Node types that count as evidence that a claim/result is supported.
EVIDENCE_NODE_TYPES = {
    "Result", "Evidence", "Finding", "Metric", "Figure", "Table", "Equation",
    "Experiment", "Observation",
}
# Node types that represent things a method is evaluated against / with.
EVALUATION_NODE_TYPES = {
    "Dataset", "Workload", "Benchmark", "Metric", "Baseline", "Result",
    "Experiment",
}

QUERY_TYPES = (
    "overview",
    "claims_without_support",
    "methods_evaluation",
    "baselines",
    "claim_evidence",
    "section_coverage",
    "entity",
)


def _is_semantic_node(node: GraphNode) -> bool:
    return node.node_type not in STRUCTURAL_NODE_TYPES


def _short(label: str, n: int = 110) -> str:
    label = (label or "").strip().replace("\n", " ")
    return label if len(label) <= n else label[: n - 1] + "…"


def _neighbors(
    graph: KnowledgeGraph,
    node_id: str,
    label_map: dict[str, str],
    type_map: dict[str, str],
) -> list[dict[str, str]]:
    """All graph neighbours of a node with edge type and direction."""
    out: list[dict[str, str]] = []
    for edge in graph.edges:
        if edge.source_id == node_id:
            other = edge.target_id
            direction = "out"
        elif edge.target_id == node_id:
            other = edge.source_id
            direction = "in"
        else:
            continue
        out.append(
            {
                "label": _short(label_map.get(other, other)),
                "type": type_map.get(other, ""),
                "edge_type": edge.edge_type,
                "direction": direction,
            }
        )
    return out


def query_graph(
    graph: KnowledgeGraph,
    query_type: str = "overview",
    *,
    target: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Answer a structured question about ``graph``.

    Returns a dict with ``query_type``, a human-readable ``summary``, a
    ``results`` payload, and ``count``. Unknown query types return an error
    payload listing the supported types rather than raising, so callers
    (including LLM tool loops) degrade gracefully.
    """
    query_type = (query_type or "overview").strip()
    limit = max(1, min(int(limit or 20), 100))

    if not isinstance(graph, KnowledgeGraph) or not graph.nodes:
        return {
            "query_type": query_type,
            "summary": "No knowledge graph entities are available.",
            "results": [],
            "count": 0,
            "available": False,
        }

    label_map = {n.id: n.label for n in graph.nodes}
    type_map = {n.id: n.node_type for n in graph.nodes}
    by_type: dict[str, list[GraphNode]] = defaultdict(list)
    for node in graph.nodes:
        by_type[node.node_type].append(node)

    if query_type == "overview":
        return _overview(graph, by_type)
    if query_type == "claims_without_support":
        return _claims_without_support(graph, by_type, label_map, type_map, limit)
    if query_type == "methods_evaluation":
        return _methods_evaluation(graph, by_type, label_map, type_map, limit)
    if query_type == "baselines":
        return _baselines(graph, by_type, label_map, type_map, target, limit)
    if query_type == "claim_evidence":
        return _claim_evidence(graph, by_type, label_map, type_map, limit)
    if query_type == "section_coverage":
        return _section_coverage(graph)
    if query_type == "entity":
        return _entity(graph, label_map, type_map, target, limit)

    return {
        "query_type": query_type,
        "summary": f"Unknown query_type {query_type!r}.",
        "supported_query_types": list(QUERY_TYPES),
        "results": [],
        "count": 0,
        "error": "unknown_query_type",
    }


def _overview(graph: KnowledgeGraph, by_type: dict[str, list[GraphNode]]) -> dict[str, Any]:
    semantic = {t: len(ns) for t, ns in by_type.items() if t not in STRUCTURAL_NODE_TYPES}
    semantic_edges = [e for e in graph.edges if e.edge_type not in STRUCTURAL_EDGE_TYPES]
    edge_types: dict[str, int] = defaultdict(int)
    for edge in semantic_edges:
        edge_types[edge.edge_type] += 1
    sections = [s for s in graph.section_names if s]
    return {
        "query_type": "overview",
        "summary": (
            f"{len(graph.nodes)} nodes, {len(graph.edges)} edges; "
            f"{sum(semantic.values())} semantic entities across "
            f"{len(semantic)} types; {len(semantic_edges)} relationship facts."
        ),
        "results": {
            "semantic_node_count_by_type": dict(sorted(semantic.items())),
            "relationship_edge_count_by_type": dict(sorted(edge_types.items())),
            "sections": sections[:30],
            "semantic_edge_count": len(semantic_edges),
        },
        "count": len(graph.nodes),
        "available": True,
    }


def _claims_without_support(
    graph: KnowledgeGraph,
    by_type: dict[str, list[GraphNode]],
    label_map: dict[str, str],
    type_map: dict[str, str],
    limit: int,
) -> dict[str, Any]:
    claims = by_type.get("Claim", [])
    unsupported: list[dict[str, Any]] = []
    supported = 0
    for claim in claims:
        neighbours = _neighbors(graph, claim.id, label_map, type_map)
        has_evidence = any(n["type"] in EVIDENCE_NODE_TYPES for n in neighbours)
        if has_evidence:
            supported += 1
            continue
        unsupported.append(
            {
                "claim": _short(claim.label),
                "section": claim.source_section,
                "neighbor_types": sorted({n["type"] for n in neighbours if n["type"]}),
            }
        )
    return {
        "query_type": "claims_without_support",
        "summary": (
            f"{len(unsupported)} of {len(claims)} extracted claims have no graph "
            f"edge to a result/metric/figure/table/equation; {supported} are linked "
            f"to evidence. Treat unlinked claims as candidates to verify in the "
            f"manuscript, not as proof of an unsupported paper claim."
        ),
        "results": unsupported[:limit],
        "count": len(unsupported),
        "available": bool(claims),
    }


def _methods_evaluation(
    graph: KnowledgeGraph,
    by_type: dict[str, list[GraphNode]],
    label_map: dict[str, str],
    type_map: dict[str, str],
    limit: int,
) -> dict[str, Any]:
    methods = by_type.get("Method", [])
    rows: list[dict[str, Any]] = []
    for method in methods[:limit]:
        neighbours = _neighbors(graph, method.id, label_map, type_map)
        grouped: dict[str, list[str]] = defaultdict(list)
        for n in neighbours:
            if n["type"] in EVALUATION_NODE_TYPES:
                grouped[n["type"]].append(n["label"])
        rows.append(
            {
                "method": _short(method.label),
                "section": method.source_section,
                "evaluation_links": {k: v for k, v in sorted(grouped.items())},
                "has_evaluation": bool(grouped),
            }
        )
    without = sum(1 for r in rows if not r["has_evaluation"])
    return {
        "query_type": "methods_evaluation",
        "summary": (
            f"{len(methods)} methods; {without} of the {len(rows)} listed have no "
            f"graph link to a dataset/workload/metric/baseline/result."
        ),
        "results": rows,
        "count": len(methods),
        "available": bool(methods),
    }


def _baselines(
    graph: KnowledgeGraph,
    by_type: dict[str, list[GraphNode]],
    label_map: dict[str, str],
    type_map: dict[str, str],
    target: str,
    limit: int,
) -> dict[str, Any]:
    baselines = by_type.get("Baseline", [])
    target_l = target.strip().lower()
    rows: list[dict[str, Any]] = []
    for baseline in baselines:
        neighbours = _neighbors(graph, baseline.id, label_map, type_map)
        compared = [
            n["label"] for n in neighbours if "COMPAR" in n["edge_type"].upper()
        ]
        linked_methods = [n["label"] for n in neighbours if n["type"] == "Method"]
        row = {
            "baseline": _short(baseline.label),
            "section": baseline.source_section,
            "compared_against": compared,
            "linked_methods": linked_methods,
        }
        if target_l:
            haystack = " ".join([baseline.label, *compared, *linked_methods]).lower()
            if target_l not in haystack:
                continue
        rows.append(row)
    return {
        "query_type": "baselines",
        "summary": (
            f"{len(baselines)} baseline entities; "
            f"{sum(1 for r in rows if r['compared_against'])} have an explicit "
            f"comparison edge."
            + (f" Filtered to {target!r}." if target_l else "")
        ),
        "results": rows[:limit],
        "count": len(rows),
        "available": bool(baselines),
    }


def _claim_evidence(
    graph: KnowledgeGraph,
    by_type: dict[str, list[GraphNode]],
    label_map: dict[str, str],
    type_map: dict[str, str],
    limit: int,
) -> dict[str, Any]:
    claims = by_type.get("Claim", [])
    rows: list[dict[str, Any]] = []
    for claim in claims[:limit]:
        neighbours = _neighbors(graph, claim.id, label_map, type_map)
        evidence = [
            {"label": n["label"], "type": n["type"]}
            for n in neighbours
            if n["type"] in EVIDENCE_NODE_TYPES
        ]
        rows.append(
            {
                "claim": _short(claim.label),
                "section": claim.source_section,
                "evidence": evidence,
            }
        )
    with_evidence = sum(1 for r in rows if r["evidence"])
    return {
        "query_type": "claim_evidence",
        "summary": (
            f"{with_evidence} of {len(rows)} listed claims have a connected "
            f"figure/table/equation/result/metric evidence node."
        ),
        "results": rows,
        "count": len(claims),
        "available": bool(claims),
    }


def _section_coverage(graph: KnowledgeGraph) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    types: dict[str, set] = defaultdict(set)
    for node in graph.nodes:
        if not _is_semantic_node(node):
            continue
        section = node.source_section or "(unattributed)"
        counts[section] += 1
        types[section].add(node.node_type)
    rows = [
        {
            "section": section,
            "entity_count": count,
            "entity_types": sorted(types[section]),
        }
        for section, count in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    return {
        "query_type": "section_coverage",
        "summary": (
            f"Semantic entities span {len(rows)} sections; "
            f"densest: {rows[0]['section']} ({rows[0]['entity_count']})."
            if rows
            else "No semantic entities are attributed to sections."
        ),
        "results": rows,
        "count": len(rows),
        "available": bool(rows),
    }


def _entity(
    graph: KnowledgeGraph,
    label_map: dict[str, str],
    type_map: dict[str, str],
    target: str,
    limit: int,
) -> dict[str, Any]:
    target_l = target.strip().lower()
    if not target_l:
        return {
            "query_type": "entity",
            "summary": "Provide a target label to look up an entity.",
            "results": [],
            "count": 0,
            "available": False,
        }
    # Prefer exact label match, then substring.
    matches = [n for n in graph.nodes if n.label.lower() == target_l]
    if not matches:
        matches = [n for n in graph.nodes if target_l in n.label.lower()]
    rows: list[dict[str, Any]] = []
    for node in matches[:limit]:
        rows.append(
            {
                "label": node.label,
                "type": node.node_type,
                "section": node.source_section,
                "description": _short(node.description, 240),
                "neighbors": _neighbors(graph, node.id, label_map, type_map),
            }
        )
    return {
        "query_type": "entity",
        "summary": (
            f"{len(matches)} entity match(es) for {target!r}."
            if matches
            else f"No entity matched {target!r}."
        ),
        "results": rows,
        "count": len(matches),
        "available": bool(matches),
    }


def graph_fact_digest(graph: KnowledgeGraph, *, max_chars: int = 1800) -> str:
    """Compact, deterministic, reviewer-safe digest of graph relationship facts.

    Used to give the deliberation phase and the PC Chair a stable set of
    positive graph facts to ground disputes against, without exposing raw edge
    counts as author-facing evidence.
    """
    if not isinstance(graph, KnowledgeGraph) or not graph.nodes:
        return ""
    methods = query_graph(graph, "methods_evaluation", limit=6)["results"]
    baselines = query_graph(graph, "baselines", limit=6)["results"]
    claim_ev = query_graph(graph, "claim_evidence", limit=6)["results"]

    lines: list[str] = []
    linked_methods = [m for m in methods if m.get("has_evaluation")]
    if linked_methods:
        lines.append("Methods with evaluation links:")
        for m in linked_methods[:6]:
            joined = "; ".join(
                f"{k}: {', '.join(v[:4])}" for k, v in m["evaluation_links"].items()
            )
            lines.append(f"- {m['method']} -> {joined}")
    compared = [b for b in baselines if b.get("compared_against")]
    if compared:
        lines.append("Baseline comparisons:")
        for b in compared[:6]:
            lines.append(f"- {b['baseline']} compared against {', '.join(b['compared_against'][:4])}")
    backed = [c for c in claim_ev if c.get("evidence")]
    if backed:
        lines.append("Claims with connected evidence:")
        for c in backed[:6]:
            ev = ", ".join(f"{e['label']} ({e['type']})" for e in c["evidence"][:3])
            lines.append(f"- {c['claim']} <- {ev}")
    if not lines:
        return ""
    digest = "\n".join(lines)
    if len(digest) > max_chars:
        digest = digest[: max_chars - 1].rsplit("\n", 1)[0] + "…"
    return digest


class GraphQueryTool:
    """Tool protocol wrapper for deterministic knowledge-graph querying.

    Bind a graph per session with ``GraphQueryTool(graph)``. The tool is only
    ``available()`` when it has a non-empty graph, so an unbound instance
    registered globally simply advertises itself as unavailable.
    """

    def __init__(self, graph: KnowledgeGraph | None = None):
        self._graph = graph

    @property
    def name(self) -> str:
        return "query_graph"

    @property
    def description(self) -> str:
        return (
            "Query the paper knowledge graph for grounded facts. query_type one "
            "of: overview, claims_without_support, methods_evaluation, baselines, "
            "claim_evidence, section_coverage, entity (entity needs target=label)."
        )

    def available(self) -> bool:
        return isinstance(self._graph, KnowledgeGraph) and bool(self._graph.nodes)

    def bound_to(self, graph: KnowledgeGraph) -> "GraphQueryTool":
        return GraphQueryTool(graph)

    async def execute(self, query: str = "", **kwargs: Any) -> ToolResult:
        query_type = str(kwargs.get("query_type") or query or "overview").strip()
        target = str(kwargs.get("target") or "")
        limit = kwargs.get("limit", 20)
        data = query_graph(self._graph, query_type, target=target, limit=limit)
        return ToolResult(data=data, source="knowledge_graph", cached=True)
