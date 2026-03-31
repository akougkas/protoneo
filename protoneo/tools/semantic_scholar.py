"""Semantic Scholar API client for citation lookup and paper metadata.

Provides reviewer agents with the ability to look up competing papers,
find top citations, and retrieve abstracts for related work analysis.

The Semantic Scholar API is free for basic queries (no key required).
An optional API key increases rate limits.
"""

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("protoneo.tools.semantic_scholar")

_BASE_URL = "https://api.semanticscholar.org/graph/v1"
_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"

_DEFAULT_FIELDS = "title,abstract,year,citationCount,authors,venue,url,externalIds"
_CITATION_FIELDS = "title,abstract,year,citationCount,authors,venue"


def _get_headers() -> dict[str, str]:
    key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    if key:
        return {"x-api-key": key}
    return {}


async def search_papers(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search for papers by keyword query.

    Returns a list of paper dicts with title, abstract, year, citation count,
    authors, venue, and URL.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(
                _SEARCH_URL,
                params={"query": query, "limit": limit, "fields": _DEFAULT_FIELDS},
                headers=_get_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
        except Exception as e:
            logger.warning("Semantic Scholar search failed for '%s': %s", query, e)
            return []


async def get_paper(paper_id: str) -> dict[str, Any] | None:
    """Get a single paper by Semantic Scholar ID, DOI, or ArXiv ID.

    Accepts formats: S2 paper ID, DOI:10.xxxx/..., ARXIV:2301.xxxxx, URL.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(
                f"{_BASE_URL}/paper/{paper_id}",
                params={"fields": _DEFAULT_FIELDS},
                headers=_get_headers(),
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("Semantic Scholar paper lookup failed for '%s': %s", paper_id, e)
            return None


async def get_paper_citations(paper_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Get papers that cite the given paper (who cites this work)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(
                f"{_BASE_URL}/paper/{paper_id}/citations",
                params={"limit": limit, "fields": _CITATION_FIELDS},
                headers=_get_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return [c.get("citingPaper", {}) for c in data.get("data", [])]
        except Exception as e:
            logger.warning("Citation lookup failed for '%s': %s", paper_id, e)
            return []


async def get_paper_references(paper_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Get papers referenced by the given paper (what this work cites)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(
                f"{_BASE_URL}/paper/{paper_id}/references",
                params={"limit": limit, "fields": _CITATION_FIELDS},
                headers=_get_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return [r.get("citedPaper", {}) for r in data.get("data", [])]
        except Exception as e:
            logger.warning("Reference lookup failed for '%s': %s", paper_id, e)
            return []


async def find_competing_papers(
    title: str,
    key_contributions: list[str] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Find papers that compete with or are closely related to the given paper.

    Searches using the paper title and key contributions to find the most
    relevant competing work. Returns papers sorted by relevance with
    citation counts for impact assessment.
    """
    queries = [title]
    if key_contributions:
        for contrib in key_contributions[:2]:
            queries.append(contrib)

    all_results: dict[str, dict] = {}
    tasks = [search_papers(q, limit=limit) for q in queries]
    results_lists = await asyncio.gather(*tasks)

    for results in results_lists:
        for paper in results:
            pid = paper.get("paperId")
            if pid and pid not in all_results:
                all_results[pid] = paper

    # Sort by citation count (most cited first) and return top N
    sorted_papers = sorted(
        all_results.values(),
        key=lambda p: p.get("citationCount", 0),
        reverse=True,
    )
    return sorted_papers[:limit]


async def build_citation_context(
    references: list[str],
    limit_per_ref: int = 1,
) -> str:
    """Build a text summary of the paper's top cited references.

    Takes reference strings extracted from the paper, searches for each
    on Semantic Scholar, and returns a formatted context string with
    titles, abstracts, and citation counts.
    """
    context_parts = []
    for ref_text in references[:10]:
        # Extract a searchable query from the reference
        # Strip "[N] " prefix and truncate
        query = ref_text.lstrip("[0123456789] ").strip()[:100]
        results = await search_papers(query, limit=limit_per_ref)
        if results:
            paper = results[0]
            authors = ", ".join(
                a.get("name", "") for a in (paper.get("authors") or [])[:3]
            )
            abstract_snippet = (paper.get("abstract") or "")[:300]
            context_parts.append(
                f"- **{paper.get('title', 'Unknown')}** ({paper.get('year', '?')})\n"
                f"  Authors: {authors}\n"
                f"  Citations: {paper.get('citationCount', 0)}\n"
                f"  Abstract: {abstract_snippet}\n"
            )
        # Rate limiting: S2 free tier allows ~100 req/5min
        await asyncio.sleep(0.3)

    if not context_parts:
        return ""

    return (
        "## Referenced Work Context (from Semantic Scholar)\n\n"
        + "\n".join(context_parts)
    )


class SemanticScholarTool:
    """Tool protocol wrapper for Semantic Scholar search."""

    @property
    def name(self) -> str:
        return "semantic_scholar"

    @property
    def description(self) -> str:
        return "Search academic papers, citations, and references via Semantic Scholar"

    def available(self) -> bool:
        return True  # Free tier always available

    async def execute(self, query: str, **kwargs: Any) -> "ToolResult":
        from .types import ToolResult

        results = await search_papers(query, limit=kwargs.get("limit", 5))
        return ToolResult(
            data={"papers": results, "query": query},
            source="semantic_scholar",
        )
