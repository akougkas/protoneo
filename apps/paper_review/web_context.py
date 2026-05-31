"""Optional web-search context for paper review.

The review agents do not yet run interactive tool loops. This module gives
them auditable web context by performing a small deterministic search pass
before review and appending the cited results to the shared review prompt.
"""

from __future__ import annotations

import os
import re
from typing import Any

from protoneo.knowledge.graph import KnowledgeGraph
from protoneo.tools.web_search import configured_backend_name, web_search


_FALSEY = {"0", "false", "no", "off", "disabled"}
_TRUEY = {"1", "true", "yes", "on", "enabled"}
_SEARCH_NODE_TYPES = {
    "Algorithm",
    "Approach",
    "Benchmark",
    "Dataset",
    "Framework",
    "Method",
    "System",
    "Tool",
}


def review_web_search_enabled() -> bool:
    """Return True when review-time web context should be gathered."""
    configured = os.getenv("PROTONEO_REVIEW_WEB_SEARCH", "auto").strip().lower()
    if configured in _FALSEY:
        return False
    if configured in _TRUEY:
        return bool(configured_backend_name())
    return bool(os.getenv("BRAVE_API_KEY") or os.getenv("SEARXNG_URL"))


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _paper_title(graph: KnowledgeGraph, fallback: str = "") -> str:
    if graph.paper_title:
        return _clean(graph.paper_title)
    root = graph.node_by_id("paper-root")
    if root:
        return _clean(root.label)
    paper = next((n for n in graph.nodes if n.node_type == "Paper"), None)
    if paper:
        return _clean(paper.label)
    return _clean(fallback)


def _important_terms(graph: KnowledgeGraph, limit: int = 4) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for node in graph.nodes:
        if node.node_type not in _SEARCH_NODE_TYPES:
            continue
        label = _clean(node.label)
        key = label.lower()
        if not label or key in seen or len(label) < 3:
            continue
        seen.add(key)
        terms.append(label)
        if len(terms) >= limit:
            break
    return terms


def _search_queries(graph: KnowledgeGraph, fallback_title: str = "") -> list[str]:
    title = _paper_title(graph, fallback_title)
    queries: list[str] = []
    if title:
        queries.append(f'"{title}"')
        queries.append(f'"{title}" HPC supercomputing')
    terms = _important_terms(graph)
    if terms:
        queries.append(" ".join(terms[:3]) + " HPC research")
    return queries[:3]


def _format_results(
    *,
    backend: str,
    query_results: list[dict[str, Any]],
) -> str:
    if not query_results:
        return ""

    lines = [
        "## External Web Search Context",
        "",
        f"Search backend: {backend}.",
        "Use this context only for related-work, novelty, landscape, and adoption checks. "
        "The manuscript and knowledge graph remain the source of truth for the paper's own claims. "
        "If a search result materially affects your assessment, mention the URL or title.",
        "",
    ]
    for block in query_results:
        lines.extend([f"### Query: {block['query']}", ""])
        for result in block["results"]:
            title = _clean(result.get("title", "Untitled"))
            url = _clean(result.get("url", ""))
            snippet = _clean(result.get("snippet", ""))
            if not title and not url:
                continue
            if url:
                lines.append(f"- {title} ({url})")
            else:
                lines.append(f"- {title}")
            if snippet:
                lines.append(f"  {snippet}")
        lines.append("")
    return "\n".join(lines).strip()


async def build_review_web_context(
    graph: KnowledgeGraph,
    *,
    fallback_title: str = "",
    count_per_query: int = 4,
) -> tuple[str, dict[str, Any]]:
    """Run the configured web search backend and return markdown + metadata."""
    backend = configured_backend_name()
    metadata: dict[str, Any] = {
        "enabled": review_web_search_enabled(),
        "backend": backend,
        "queries": [],
        "result_count": 0,
    }
    if not metadata["enabled"] or not backend:
        metadata["enabled"] = False
        return "", metadata

    query_results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for query in _search_queries(graph, fallback_title):
        results = []
        for result in await web_search(query, count=count_per_query):
            data = result.to_dict()
            url = _clean(data.get("url", ""))
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            results.append(data)
        if results:
            query_results.append({"query": query, "results": results})

    metadata["queries"] = [block["query"] for block in query_results]
    metadata["result_count"] = sum(len(block["results"]) for block in query_results)
    metadata["backend"] = backend
    return _format_results(backend=backend, query_results=query_results), metadata
