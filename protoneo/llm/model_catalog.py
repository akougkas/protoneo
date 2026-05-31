"""Normalized provider/model catalog.

The persisted settings keep provider configuration, discovery cache, and active
selections separate. This module is the adapter that turns those pieces into
the single model shape used by API routes and the frontend.
"""

from __future__ import annotations

from typing import Any

from .errors import classify_model_error
from .registry import CapabilityRegistry
from .settings import ProtoNeoSettings, endpoint_map, provider_is_enabled
from .types import ModelCapability, ModelInfo, ModelTier

SUPPORTED_REASONING_EFFORTS = ["low", "medium", "high", "xhigh"]


def provider_model_id(provider_id: str, model_id: str) -> str:
    """Return ProtoNeo's provider-prefixed model identifier."""
    return model_id if model_id.startswith(f"{provider_id}/") else f"{provider_id}/{model_id}"


def raw_model_id(provider_id: str, model_id: str) -> str:
    prefix = f"{provider_id}/"
    return model_id[len(prefix):] if model_id.startswith(prefix) else model_id


def entry_context_length(entry: dict[str, Any], info: ModelInfo) -> int:
    for key in ("context_length", "max_context_length", "context_window", "max_context", "ctx_size", "n_ctx"):
        try:
            value = int(entry.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return info.max_context if info.max_context > 0 else 0


def supports_reasoning_effort(provider_id: str, model_id: str, info: ModelInfo) -> bool:
    """Whether ProtoNeo can safely expose explicit reasoning-effort control."""
    if ModelCapability.REASONING_CONTROL in info.capabilities:
        return True
    if provider_id != "openai":
        return False
    lower = model_id.lower()
    return (
        lower.startswith("gpt-5")
        or lower.startswith(("o1", "o3", "o4"))
        or "codex" in lower
    )


def _entry_reasoning_efforts(entry: dict[str, Any]) -> list[str]:
    raw = entry.get("supported_reasoning_efforts") or entry.get("supported_reasoning_levels") or []
    efforts: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                effort = str(item.get("effort") or "").strip()
            else:
                effort = str(item or "").strip()
            if effort and effort not in efforts:
                efforts.append(effort)
    return [effort for effort in SUPPORTED_REASONING_EFFORTS if effort in efforts]


def _entry_availability(entry: dict[str, Any]) -> tuple[str, str]:
    availability = str(entry.get("availability") or "").strip()
    if not availability:
        availability = "available"
    reason = str(entry.get("availability_reason") or "").strip()
    return availability, reason


def _provider_health(settings: ProtoNeoSettings) -> dict[str, dict[str, Any]]:
    """Infer provider-wide health from recent persisted benchmark failures."""
    health: dict[str, dict[str, Any]] = {}
    for result in settings.benchmark_results or []:
        if not isinstance(result, dict):
            continue
        provider_id = str(result.get("provider") or "")
        if not provider_id:
            continue
        messages = [str(result.get("error") or "")]
        dimensions = result.get("dimensions") or {}
        if isinstance(dimensions, dict):
            for dim in dimensions.values():
                if isinstance(dim, dict) and dim.get("error"):
                    messages.append(str(dim.get("error") or ""))

        for message in messages:
            status, summary = classify_model_error(message)
            if not status:
                continue
            if status == "quota_limited":
                health[provider_id] = {
                    "health_status": status,
                    "health_message": summary,
                    "review_routable": False,
                }
                break
            if provider_id not in health:
                health[provider_id] = {
                    "health_status": status,
                    "health_message": summary,
                    "review_routable": True,
                }
    return health


def normalize_model_entry(
    provider_id: str,
    entry: dict[str, Any],
    *,
    settings: ProtoNeoSettings,
    registry: CapabilityRegistry,
    source: str = "cache",
) -> dict[str, Any]:
    """Normalize one discovered or custom model entry for API/UI use."""
    model_id = str(entry.get("id") or entry.get("model_id") or "").strip()
    if not model_id:
        raise ValueError("model entry missing id")

    raw_id = raw_model_id(provider_id, model_id)
    qualified_id = provider_model_id(provider_id, raw_id)
    info = registry.get(qualified_id)
    capabilities = sorted(capability.value for capability in info.capabilities)
    context_length = entry_context_length(entry, info)
    supports_effort = supports_reasoning_effort(provider_id, raw_id, info)
    entry_efforts = _entry_reasoning_efforts(entry)
    availability, availability_reason = _entry_availability(entry)
    endpoint = endpoint_map(settings).get(provider_id)
    tier = info.tier.value
    is_local = info.tier == ModelTier.LOCAL or endpoint is not None
    prompt_price = float(entry.get("cost_prompt") or entry.get("cost_input") or info.cost_per_input_token or 0.0)
    completion_price = float(entry.get("cost_completion") or entry.get("cost_output") or info.cost_per_output_token or 0.0)
    has_pricing = any(
        key in entry
        for key in ("cost_prompt", "cost_input", "cost_completion", "cost_output")
    )
    if "is_free" in entry:
        is_free = bool(entry.get("is_free"))
    elif is_local:
        is_free = True
    elif provider_id == "openrouter" and raw_id.endswith(":free"):
        is_free = True
    elif has_pricing:
        is_free = prompt_price == 0 and completion_price == 0
    else:
        is_free = False

    return {
        "provider_id": provider_id,
        "model_id": raw_id,
        "provider_model_id": qualified_id,
        "qualified_id": qualified_id,
        "display_name": str(entry.get("display_name") or entry.get("name") or info.display_name or raw_id),
        "source": str(entry.get("source") or provider_id),
        "discovery_source": str(entry.get("discovery_source") or source),
        "provider_type": str(entry.get("provider_type") or ("local" if is_local else tier)),
        "capabilities": capabilities,
        "context_length": context_length,
        "context_source": str(entry.get("context_source") or ("registry" if info.max_context else "")),
        "catalog_priority": entry.get("catalog_priority"),
        "max_context": context_length,
        "loaded": entry.get("loaded"),
        "loaded_status": "loaded" if entry.get("loaded") else ("unloaded" if entry.get("loaded") is False else ""),
        "is_free": is_free,
        "pricing": {
            "prompt": prompt_price,
            "completion": completion_price,
        },
        "cost_prompt": prompt_price,
        "cost_completion": completion_price,
        "tier": tier,
        "runtime_location": info.runtime_location,
        "latency_class": info.latency_class.value,
        "structured_output": info.structured_output.value,
        "supports_reasoning": ModelCapability.EXTENDED_THINKING.value in capabilities,
        "supports_reasoning_effort": supports_effort,
        "supported_reasoning_efforts": entry_efforts or (SUPPORTED_REASONING_EFFORTS if supports_effort else []),
        "default_reasoning_effort": str(entry.get("default_reasoning_effort") or ""),
        "supports_tools": ModelCapability.FUNCTION_CALLING.value in capabilities,
        "supports_vision": ModelCapability.VISION.value in capabilities,
        "speed_tps": info.speed_tps,
        "is_private": info.is_private,
        "enabled": provider_is_enabled(provider_id, settings),
        "custom": bool(entry.get("custom", False)),
        "availability": availability,
        "availability_reason": availability_reason,
        "health_status": str(entry.get("health_status") or ""),
        "health_message": str(entry.get("health_message") or ""),
        "review_routable": bool(entry.get("review_routable", True)),
        "supported_in_api": entry.get("supported_in_api"),
        "standard_openai_api_supported": entry.get("standard_openai_api_supported", entry.get("supported_in_api")),
        "catalog_visibility": str(entry.get("catalog_visibility") or ""),
    }


def build_model_catalog(
    settings: ProtoNeoSettings,
    registry: CapabilityRegistry | None = None,
) -> list[dict[str, Any]]:
    """Build a deduplicated normalized catalog from discovery cache + selections."""
    active_registry = registry or CapabilityRegistry.from_settings(settings)
    catalog_by_id: dict[str, dict[str, Any]] = {}

    for provider_id, models in (settings.discovered_models or {}).items():
        if not isinstance(models, list):
            continue
        for entry in models:
            if not isinstance(entry, dict):
                continue
            try:
                normalized = normalize_model_entry(
                    str(provider_id),
                    entry,
                    settings=settings,
                    registry=active_registry,
                    source=str(entry.get("discovery_source") or "cache"),
                )
            except ValueError:
                continue
            catalog_by_id[normalized["provider_model_id"]] = normalized

    # Keep custom/active choices visible even before discovery succeeds.
    for provider_id, selected_model in (settings.active_models or {}).items():
        if not selected_model:
            continue
        qualified_id = provider_model_id(provider_id, selected_model)
        if qualified_id in catalog_by_id:
            continue
        normalized = normalize_model_entry(
            provider_id,
            {
                "id": selected_model,
                "name": selected_model,
                "source": provider_id,
                "custom": True,
                "discovery_source": "active_selection",
            },
            settings=settings,
            registry=active_registry,
            source="active_selection",
        )
        catalog_by_id[qualified_id] = normalized

    health = _provider_health(settings)
    for model in catalog_by_id.values():
        provider_health = health.get(str(model.get("provider_id") or ""))
        if provider_health:
            model.update(provider_health)

    return sorted(
        catalog_by_id.values(),
        key=lambda model: (
            0 if model["enabled"] else 1,
            model["provider_id"],
            int(model.get("catalog_priority") or 999_999),
            model["display_name"].lower(),
        ),
    )


def cache_from_discovery_results(
    results: dict[str, Any],
    previous_cache: dict[str, list[dict[str, Any]]] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, bool]]:
    """Extract provider->models cache from discovery results.

    Returns ``(cache_updates, live_success)``. A provider is marked as live only
    when the result came from the provider endpoint/API, not from cached fallback.
    """
    cache: dict[str, list[dict[str, Any]]] = {}
    live_success: dict[str, bool] = {}
    previous_cache = previous_cache or {}

    for group_name in ("localhost", "lan"):
        nodes = results.get(group_name, [])
        if not isinstance(nodes, list):
            continue
        for node in nodes:
            if not isinstance(node, dict):
                continue
            provider_id = str(node.get("id") or node.get("name") or "")
            if not provider_id:
                continue
            is_live = bool(node.get("online")) and not bool(node.get("using_cache"))
            models = []
            for model in node.get("models", []):
                if not isinstance(model, dict):
                    continue
                entry = dict(model)
                entry["source"] = provider_id
                entry["discovery_source"] = entry.get("discovery_source") or ("live" if is_live else "cache")
                models.append(entry)
            if is_live or provider_id not in previous_cache:
                cache[provider_id] = models
            live_success[provider_id] = is_live

    for provider_id, value in results.items():
        if provider_id in {"localhost", "lan", "catalog"}:
            continue
        if not isinstance(value, dict) or "models" not in value:
            continue
        is_live = bool(value.get("online")) and not bool(value.get("using_cache"))
        models = []
        for model in value.get("models", []):
            if not isinstance(model, dict):
                continue
            entry = dict(model)
            entry["source"] = provider_id
            entry["discovery_source"] = entry.get("discovery_source") or ("live" if is_live else "cache")
            models.append(entry)
        if is_live or provider_id not in previous_cache:
            cache[provider_id] = models
        live_success[provider_id] = is_live

    return cache, live_success
