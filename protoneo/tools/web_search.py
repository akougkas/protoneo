"""Web search client for reviewer agents.

Provides configurable web search via multiple backends:
- Brave Search API (requires BRAVE_API_KEY)
- SearXNG self-hosted instance (requires SEARXNG_URL)
- DuckDuckGo (opt-in fallback with PROTONEO_ENABLE_DUCKDUCKGO_SEARCH=1)

Reviewers use web search to check research trends, verify claims,
and find context beyond the paper's reference list.
"""

import logging
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

logger = logging.getLogger("protoneo.tools.web_search")

_TRUEY = {"1", "true", "yes", "on", "enabled"}
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_PROJECT_ROOT / ".env", override=False)


def _timeout_seconds(env_name: str, default: float) -> float:
    try:
        return max(1.0, float(os.getenv(env_name, str(default))))
    except ValueError:
        return default


class SearchResult:
    """A single web search result."""
    def __init__(self, title: str, url: str, snippet: str):
        self.title = title
        self.url = url
        self.snippet = snippet

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


async def search_brave(query: str, count: int = 5) -> list[SearchResult]:
    """Search via Brave Search API."""
    api_key = os.getenv("BRAVE_API_KEY")
    if not api_key:
        return []
    async with httpx.AsyncClient(timeout=_timeout_seconds("BRAVE_SEARCH_TIMEOUT_SECONDS", 10.0)) as client:
        try:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": count},
                headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in data.get("web", {}).get("results", [])[:count]:
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                ))
            return results
        except Exception as e:
            logger.warning("Brave search failed (%s): %s", type(e).__name__, e)
            return []


async def search_searxng(query: str, count: int = 5) -> list[SearchResult]:
    """Search via self-hosted SearXNG instance."""
    base_url = os.getenv("SEARXNG_URL")
    if not base_url:
        return []
    async with httpx.AsyncClient(timeout=_timeout_seconds("SEARXNG_TIMEOUT_SECONDS", 5.0)) as client:
        try:
            resp = await client.get(
                f"{base_url.rstrip('/')}/search",
                params={"q": query, "format": "json", "categories": "general,science"},
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in data.get("results", [])[:count]:
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                ))
            return results
        except Exception as e:
            logger.warning("SearXNG search failed (%s): %s", type(e).__name__, e)
            return []


async def search_duckduckgo(query: str, count: int = 5) -> list[SearchResult]:
    """Search via DuckDuckGo HTML (no API key needed, rate-limited)."""
    async with httpx.AsyncClient(
        timeout=_timeout_seconds("DUCKDUCKGO_SEARCH_TIMEOUT_SECONDS", 10.0),
        follow_redirects=True,
    ) as client:
        try:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "ProtoNeo/1.0 (Academic Review Tool)"},
            )
            resp.raise_for_status()
            # Simple HTML parsing for result snippets
            import re
            results = []
            # Extract result blocks
            for match in re.finditer(
                r'class="result__a"[^>]*href="([^"]*)"[^>]*>([^<]*)</a>.*?'
                r'class="result__snippet"[^>]*>(.*?)</span>',
                resp.text, re.DOTALL,
            ):
                if len(results) >= count:
                    break
                url = match.group(1)
                title = match.group(2).strip()
                snippet = re.sub(r"<[^>]+>", "", match.group(3)).strip()
                results.append(SearchResult(title=title, url=url, snippet=snippet))
            return results
        except Exception as e:
            logger.warning("DuckDuckGo search failed (%s): %s", type(e).__name__, e)
            return []


async def web_search(query: str, count: int = 5) -> list[SearchResult]:
    """Search the web using the best available backend.

    Tries backends in priority order: Brave > SearXNG > DuckDuckGo.
    DuckDuckGo is only used when explicitly enabled by environment variable.
    Returns the first successful result set.
    """
    if os.getenv("BRAVE_API_KEY"):
        results = await search_brave(query, count)
        if results:
            return results

    if os.getenv("SEARXNG_URL"):
        results = await search_searxng(query, count)
        if results:
            return results

    if os.getenv("PROTONEO_ENABLE_DUCKDUCKGO_SEARCH", "").strip().lower() in _TRUEY:
        return await search_duckduckgo(query, count)
    return []


def configured_backend_name() -> str:
    """Return the first configured web-search backend name."""
    if os.getenv("BRAVE_API_KEY"):
        return "brave"
    if os.getenv("SEARXNG_URL"):
        return "searxng"
    if os.getenv("PROTONEO_ENABLE_DUCKDUCKGO_SEARCH", "").strip().lower() in _TRUEY:
        return "duckduckgo"
    return ""


def is_available() -> bool:
    """Check if any web search backend is configured."""
    return bool(configured_backend_name())


async def search_research_trends(topic: str, count: int = 5) -> str:
    """Search for research trends on a topic and return formatted context.

    Used by reviewers to understand the current landscape around
    a paper's contributions.
    """
    results = await web_search(f"{topic} research trends 2025 2026", count)
    if not results:
        return ""

    parts = [f"## Research Trends: {topic}\n"]
    for r in results:
        parts.append(f"- [{r.title}]({r.url})\n  {r.snippet}\n")
    return "\n".join(parts)


class WebSearchTool:
    """Tool protocol wrapper for web search."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web via Brave, SearXNG, or DuckDuckGo"

    def available(self) -> bool:
        return is_available()

    async def execute(self, query: str, **kwargs: Any) -> "ToolResult":
        from .types import ToolResult

        results = await web_search(query, count=kwargs.get("count", 5))
        return ToolResult(
            data={"results": [r.to_dict() for r in results], "query": query},
            source="web_search",
        )
