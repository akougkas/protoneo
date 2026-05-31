"""Seed model catalogs for subscription providers.

ChatGPT OAuth credentials do not expose the same model enumeration surface as
standard OpenAI API keys. For that account path, ProtoNeo prefers the local
Codex CLI model cache because it reflects the user's ChatGPT/Codex account
catalog. The bundled list below is only a clearly labeled fallback when that
local cache is unavailable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# DISABLED: Anthropic provider removed from ProtoNeo.
# Claude Max subscription models preserved as reference only.
# ANTHROPIC_SUBSCRIPTION_MODELS = [
#     {"id": "claude-opus-4-6", "name": "Claude Opus 4.6", "context_length": 1_000_000, "reasoning": True, "source": "anthropic", "provider_type": "subscription"},
#     {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "context_length": 200_000, "reasoning": True, "source": "anthropic", "provider_type": "subscription"},
#     {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5", "context_length": 200_000, "reasoning": False, "source": "anthropic", "provider_type": "subscription"},
# ]
ANTHROPIC_SUBSCRIPTION_MODELS: list = []  # Empty: provider disabled

CODEX_MODELS_CACHE = Path.home() / ".codex" / "models_cache.json"
SUPPORTED_REASONING_EFFORTS = ["low", "medium", "high", "xhigh"]


def _coerce_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _reasoning_efforts(value: Any) -> list[str]:
    efforts: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                effort = str(item.get("effort") or "").strip()
            else:
                effort = str(item or "").strip()
            if effort and effort not in efforts:
                efforts.append(effort)
    return [effort for effort in SUPPORTED_REASONING_EFFORTS if effort in efforts]


def _openai_reasoning_model(model_id: str) -> bool:
    lower = model_id.lower()
    return lower.startswith("gpt-5") or lower.startswith(("o1", "o3", "o4")) or "codex" in lower


def _fallback_model(model_id: str, name: str, context_length: int, *, availability: str = "unverified") -> dict[str, Any]:
    return {
        "id": model_id,
        "name": name,
        "display_name": name,
        "context_length": context_length,
        "reasoning": _openai_reasoning_model(model_id),
        "source": "openai",
        "provider_type": "subscription",
        "availability": availability,
        "availability_reason": "Bundled ChatGPT/Codex fallback; refresh Codex CLI login/cache to verify account availability.",
        "supports_reasoning_effort": _openai_reasoning_model(model_id),
        "supported_reasoning_efforts": SUPPORTED_REASONING_EFFORTS if _openai_reasoning_model(model_id) else [],
        "default_reasoning_effort": "medium" if _openai_reasoning_model(model_id) else "",
    }


# Conservative ChatGPT/Codex subscription fallback. Do not add general OpenAI
# API models here; they are discovered through the API-key path, not OAuth.
OPENAI_SUBSCRIPTION_MODELS = [
    _fallback_model("gpt-5.5", "GPT-5.5", 272_000),
    _fallback_model("gpt-5.4", "GPT-5.4", 272_000),
    _fallback_model("gpt-5.4-mini", "GPT-5.4 Mini", 272_000),
    _fallback_model("gpt-5.3-codex", "GPT-5.3 Codex", 272_000),
    _fallback_model("gpt-5.2", "GPT-5.2", 272_000),
]


def codex_cache_model(entry: dict[str, Any], *, include_hidden: bool = False) -> dict[str, Any] | None:
    """Normalize one model entry from ``~/.codex/models_cache.json``."""
    model_id = str(entry.get("slug") or "").strip()
    if not model_id:
        return None

    visibility = str(entry.get("visibility") or "list").strip() or "list"
    if visibility == "hide" and not include_hidden:
        return None

    display_name = str(entry.get("display_name") or model_id).strip()
    supported_in_api = entry.get("supported_in_api")
    efforts = _reasoning_efforts(entry.get("supported_reasoning_levels"))
    context_length = _coerce_int(entry.get("context_window") or entry.get("max_context_window"))
    max_context = _coerce_int(entry.get("max_context_window"))
    availability = "available"
    availability_reason = ""
    if visibility == "hide":
        availability = "unsupported"
        availability_reason = "Codex catalog hides this model from normal selection."
    elif supported_in_api is False:
        availability_reason = "Codex CLI account model; routed through the ChatGPT/Codex subscription endpoint, not the standard OpenAI API."

    normalized = {
        "id": model_id,
        "name": display_name,
        "display_name": display_name,
        "context_length": context_length,
        "max_context_length": max_context,
        "catalog_priority": _coerce_int(entry.get("priority")),
        "effective_context_window_percent": entry.get("effective_context_window_percent"),
        "reasoning": bool(efforts) or _openai_reasoning_model(model_id),
        "source": "openai",
        "provider_type": "subscription",
        "availability": availability,
        "availability_reason": availability_reason,
        "catalog_visibility": visibility,
        "supported_in_api": supported_in_api,
        "standard_openai_api_supported": supported_in_api,
        "supports_reasoning_effort": bool(efforts) or _openai_reasoning_model(model_id),
        "supported_reasoning_efforts": efforts or (SUPPORTED_REASONING_EFFORTS if _openai_reasoning_model(model_id) else []),
        "default_reasoning_effort": str(entry.get("default_reasoning_level") or "").strip(),
    }
    return normalized


def load_codex_cli_models(
    cache_path: Path | None = None,
    *,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    """Load available ChatGPT/Codex models from the Codex CLI cache."""
    path = cache_path or CODEX_MODELS_CACHE
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        return []

    models: list[dict[str, Any]] = []
    for entry in raw_models:
        if not isinstance(entry, dict):
            continue
        model = codex_cache_model(entry, include_hidden=include_hidden)
        if model is not None:
            models.append(model)

    return sorted(
        models,
        key=lambda model: (
            model.get("availability") == "unsupported",
            _coerce_int(model.get("catalog_priority")) or 999_999,
            str(model.get("id") or ""),
        ),
    )


# Removed stale synthetic entries from OPENAI_SUBSCRIPTION_MODELS:
#
# - gpt-5.5-codex: previously seeded, but the ChatGPT Codex backend rejected it
#   for this account path and it is absent from the local Codex cache.
# - broad gpt-3/4/o-series API models: those come from the OpenAI API catalog,
#   not from ChatGPT OAuth subscription discovery.

SUBSCRIPTION_CATALOGS = {
    "anthropic": ANTHROPIC_SUBSCRIPTION_MODELS,
    "openai": OPENAI_SUBSCRIPTION_MODELS,
}
