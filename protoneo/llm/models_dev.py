"""Runtime model catalog from models.dev.

Fetches the same data source that pi-ai uses at build time (models.dev/api.json)
but queries it at runtime. Results are cached to disk so we only hit the API once
per session (or when explicitly refreshed).

This is the discovery source for subscription providers (OpenAI)
whose OAuth tokens cannot enumerate models via /v1/models.
Anthropic provider has been disabled.

models.dev JSON structure:
    {
        "openai": { ... },
        ...
    }
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("protoneo.llm.models_dev")

_MODELS_DEV_URL = "https://models.dev/api.json"
_CACHE_DIR = Path.home() / ".protoneo"
_CACHE_FILE = _CACHE_DIR / "models_dev_cache.json"
_CACHE_TTL = 3600 * 6  # 6 hours

# Chat model prefixes per provider (filter out embedding, tts, image models)
_CHAT_PREFIXES = {
    # "anthropic": ("claude-",),  # DISABLED: Anthropic provider removed
    "openai": ("gpt-", "o1", "o3", "o4", "chatgpt-"),
}

# Skip models containing these substrings (embeddings, previews with weird suffixes)
_SKIP_SUBSTRINGS = (
    "embedding",
    "tts",
    "dall-e",
    "whisper",
    "moderation",
    "image",
    "audio",
    "realtime",
)


def _is_chat_model(provider: str, model_id: str) -> bool:
    """Filter to only chat/reasoning models."""
    mid_lower = model_id.lower()
    if any(s in mid_lower for s in _SKIP_SUBSTRINGS):
        return False
    prefixes = _CHAT_PREFIXES.get(provider, ())
    if not prefixes:
        return True
    return any(model_id.startswith(p) for p in prefixes)


def _load_cache(*, force_refresh: bool = False) -> dict[str, Any] | None:
    """Load cached models.dev data if still fresh."""
    if force_refresh:
        return None
    if not _CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_CACHE_FILE.read_text())
        if time.time() - data.get("_fetched_at", 0) < _CACHE_TTL:
            return data
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _save_cache(data: dict[str, Any]) -> None:
    """Persist models.dev data to disk."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data["_fetched_at"] = time.time()
    _CACHE_FILE.write_text(json.dumps(data, indent=2))


async def fetch_models_dev(*, force_refresh: bool = False) -> dict[str, Any] | None:
    """Fetch the models.dev catalog, using cache if fresh.

    Returns the raw JSON dict keyed by provider name, or None on failure.
    """
    cached = _load_cache(force_refresh=force_refresh)
    if cached:
        logger.debug("Using cached models.dev data")
        return cached

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(_MODELS_DEV_URL)
            if resp.status_code != 200:
                logger.warning("models.dev returned %d", resp.status_code)
                return None
            data = resp.json()
            _save_cache(data)
            logger.info("Fetched models.dev catalog (%d providers)", len(data))
            return data
        except Exception as e:
            logger.warning("Failed to fetch models.dev: %s", e)
            return None


def parse_provider_models(
    raw_data: dict[str, Any],
    provider: str,
) -> list[dict[str, Any]]:
    """Extract models for a specific provider from models.dev data.

    Returns a list of model dicts in our standard discovery format:
    [{"id": ..., "name": ..., "context_length": ..., "reasoning": ..., ...}]
    """
    provider_data = raw_data.get(provider, {})
    if not isinstance(provider_data, dict):
        return []

    models_dict = provider_data.get("models", {})
    if not isinstance(models_dict, dict):
        return []

    models = []
    for model_id, entry in models_dict.items():
        if not isinstance(entry, dict):
            continue
        if not _is_chat_model(provider, model_id):
            continue

        # Context window: entry["limit"]["context"]
        limit = entry.get("limit", {})
        context_length = 0
        if isinstance(limit, dict):
            context_length = limit.get("context", 0) or 0

        reasoning = entry.get("reasoning", False)
        name = entry.get("name", model_id)

        # Cost: entry["cost"]["input"], entry["cost"]["output"]
        cost = entry.get("cost", {})
        cost_input = 0
        cost_output = 0
        if isinstance(cost, dict):
            cost_input = cost.get("input", 0) or 0
            cost_output = cost.get("output", 0) or 0

        models.append({
            "id": model_id,
            "name": name,
            "context_length": context_length,
            "reasoning": reasoning,
            "source": provider,
            "provider_type": "subscription",
            "cost_input": cost_input,
            "cost_output": cost_output,
        })

    # Sort by name for consistent ordering
    models.sort(key=lambda m: m["name"])
    return models


async def discover_provider_models(
    provider: str,
    *,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Discover models for a subscription provider from models.dev.

    This is the primary discovery path for OAuth-authenticated providers
    whose tokens cannot enumerate models via their own APIs.
    """
    raw = await fetch_models_dev(force_refresh=force_refresh)
    if raw is None:
        return []
    return parse_provider_models(raw, provider)


def build_context_window_map(
    raw_data: dict[str, Any],
    provider: str,
) -> dict[str, int]:
    """Build a model_id -> context_window lookup from models.dev data.

    Used to augment/correct context windows from provider APIs that
    return stale values (e.g., Anthropic /v1/models reports 200k for
    models that actually support 1M).
    """
    result = {}
    for model in parse_provider_models(raw_data, provider):
        if model["context_length"]:
            result[model["id"]] = model["context_length"]
    return result
