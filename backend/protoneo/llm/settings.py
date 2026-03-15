"""ProtoNeo settings persistence.

Stores user preferences to ~/.protoneo/settings.json. Includes model
assignments for review roles, tool toggles, and benchmark results.
"""

import json
import logging
import os
import re
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

logger = logging.getLogger("protoneo.llm.settings")

_SETTINGS_DIR = Path.home() / ".protoneo"
_SETTINGS_FILE = _SETTINGS_DIR / "settings.json"

_LOCALHOST = "localhost"
_LAN = "lan"

# Backward-compatibility aliases for settings migration.
# Old settings.json files may reference these short names.
_LEGACY_PROVIDER_IDS = {
    "zbook": "localhost-lmstudio",
    "ollama": "localhost-ollama",
    "mini": "lan-mini",
    "dynamo": "lan-dynamo",
}

_KNOWN_ENDPOINTS = {
    "localhost-lmstudio": {
        "display_name": "LM Studio",
        "location": _LOCALHOST,
        "type": "openai",
    },
    "localhost-ollama": {
        "display_name": "Ollama",
        "location": _LOCALHOST,
        "type": "ollama",
    },
}

_KNOWN_HOSTS = {
    "localhost": "localhost-lmstudio",
    "127.0.0.1": "localhost-lmstudio",
    "::1": "localhost-lmstudio",
}


class LocalEndpoint(BaseModel):
    """A configured LLM service endpoint."""

    id: str
    display_name: str
    url: str
    type: str = "openai"  # "openai" or "ollama"
    enabled: bool = True
    location: str = _LOCALHOST  # "localhost" or "lan"


class ProtoNeoSettings(BaseModel):
    """Root settings object persisted to disk.

    This is ProtoNeo runtime configuration, not per-review settings.
    Per-review settings (role assignments, tool toggles) live in the
    review session config and are set in PanelHome.
    """

    localhost_endpoints: list[LocalEndpoint] = Field(default_factory=lambda: [
        LocalEndpoint(
            id="localhost-lmstudio",
            display_name="LM Studio",
            url="http://localhost:1234/v1",
            type="openai",
            location=_LOCALHOST,
        ),
        LocalEndpoint(
            id="localhost-ollama",
            display_name="Ollama",
            url="http://localhost:11434",
            type="ollama",
            location=_LOCALHOST,
        ),
    ])
    lan_endpoints: list[LocalEndpoint] = Field(default_factory=list)
    openrouter_free_only: bool = True
    provider_enabled: dict[str, bool] = Field(default_factory=dict)
    active_models: dict[str, str] = Field(default_factory=dict)
    benchmark_results: list[dict[str, Any]] = Field(default_factory=list)
    discovered_models: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "endpoint"


def _host_from_url(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _infer_location(url: str, fallback: str = _LOCALHOST) -> str:
    host = _host_from_url(url)
    if host in {"", "localhost", "127.0.0.1", "::1"}:
        return _LOCALHOST
    try:
        if ip_address(host).is_private:
            return _LAN
    except ValueError:
        pass
    if host.startswith("192.168.") or host.startswith("10.") or host.endswith(".local"):
        return _LAN
    return fallback


def _canonical_endpoint_id(
    name_hint: str,
    url: str,
    endpoint_type: str,
    location: str,
) -> str:
    lowered_hint = _slugify(name_hint)
    host = _host_from_url(url)
    port = urlparse(url).port

    if lowered_hint in _LEGACY_PROVIDER_IDS:
        return _LEGACY_PROVIDER_IDS[lowered_hint]

    if location == _LOCALHOST:
        if endpoint_type == "ollama" or lowered_hint == "ollama" or port == 11434:
            return "localhost-ollama"
        if host in {"localhost", "127.0.0.1", "::1"} and port == 1234:
            return "localhost-lmstudio"
        if lowered_hint.startswith("localhost-"):
            return lowered_hint
        return f"localhost-{lowered_hint}"

    if host in _KNOWN_HOSTS:
        return _KNOWN_HOSTS[host]
    if lowered_hint.startswith("lan-"):
        return lowered_hint
    return f"lan-{lowered_hint}"


def _default_display_name(endpoint_id: str, name_hint: str, endpoint_type: str) -> str:
    if endpoint_id in _KNOWN_ENDPOINTS:
        return str(_KNOWN_ENDPOINTS[endpoint_id]["display_name"])
    if endpoint_type == "ollama" and not name_hint:
        return "Ollama"
    return name_hint or endpoint_id


def _normalize_endpoint_payload(
    payload: Any,
    default_location: str,
) -> dict[str, Any] | None:
    if isinstance(payload, LocalEndpoint):
        return payload.model_dump()
    if not isinstance(payload, dict):
        return None

    url = str(payload.get("url") or "").strip()
    if not url:
        return None

    endpoint_type = str(payload.get("type") or "openai")
    raw_id = str(payload.get("id") or "").strip()
    raw_name = str(payload.get("name") or "").strip()
    raw_display_name = str(payload.get("display_name") or "").strip()
    location = str(payload.get("location") or "").strip() or _infer_location(url, default_location)
    location = _LOCALHOST if location == _LOCALHOST else _LAN

    endpoint_id = raw_id
    if not endpoint_id or endpoint_id in _LEGACY_PROVIDER_IDS:
        endpoint_id = _canonical_endpoint_id(raw_name or raw_display_name or raw_id or endpoint_type, url, endpoint_type, location)

    defaults = _KNOWN_ENDPOINTS.get(endpoint_id, {})
    display_name = raw_display_name or _default_display_name(
        endpoint_id,
        raw_name or raw_id,
        endpoint_type,
    )

    return {
        "id": endpoint_id,
        "display_name": display_name or str(defaults.get("display_name") or endpoint_id),
        "url": url,
        "type": endpoint_type or str(defaults.get("type") or "openai"),
        "enabled": bool(payload.get("enabled", True)),
        "location": location or str(defaults.get("location") or default_location),
    }


def _collect_endpoints(
    raw_endpoints: list[Any],
    default_location: str,
    target: dict[str, dict[str, Any]],
    aliases: dict[str, str],
) -> None:
    for raw_endpoint in raw_endpoints:
        normalized = _normalize_endpoint_payload(raw_endpoint, default_location)
        if normalized is None:
            continue

        if isinstance(raw_endpoint, dict):
            for legacy_key in (
                str(raw_endpoint.get("id") or "").strip(),
                str(raw_endpoint.get("name") or "").strip(),
            ):
                if legacy_key:
                    aliases[legacy_key] = normalized["id"]

        target[normalized["id"]] = normalized


def _migrate_discovered_models(
    discovered: Any,
    aliases: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(discovered, dict):
        return {}

    migrated: dict[str, list[dict[str, Any]]] = {}
    group_keys = {"local", "localhost", "homelab", "lan"}

    for bucket, models in discovered.items():
        if not isinstance(models, list):
            continue

        mapped_bucket = aliases.get(str(bucket), str(bucket))
        for entry in models:
            if not isinstance(entry, dict):
                continue

            item = dict(entry)
            source = str(item.get("source") or "")
            if source:
                source = aliases.get(source, source)
            elif mapped_bucket not in group_keys:
                source = mapped_bucket
            else:
                continue

            item["source"] = source
            migrated.setdefault(source, []).append(item)

    return migrated


def _migrate_settings_data(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}

    aliases = dict(_LEGACY_PROVIDER_IDS)
    localhost_by_id: dict[str, dict[str, Any]] = {}
    lan_by_id: dict[str, dict[str, Any]] = {}

    for bucket_key, default_location in (
        ("local_endpoints", _LOCALHOST),
        ("homelab_endpoints", _LAN),
        ("localhost_endpoints", _LOCALHOST),
        ("lan_endpoints", _LAN),
    ):
        bucket = data.get(bucket_key) or []
        if not isinstance(bucket, list):
            continue

        target = localhost_by_id if default_location == _LOCALHOST else lan_by_id
        _collect_endpoints(bucket, default_location, target, aliases)

    # Honor URL-derived location when old local_endpoints mix localhost and LAN.
    misplaced: list[tuple[str, dict[str, Any]]] = []
    for endpoint_id, endpoint in localhost_by_id.items():
        inferred = _infer_location(endpoint["url"], endpoint["location"])
        if inferred == _LAN:
            endpoint["location"] = _LAN
            misplaced.append((endpoint_id, endpoint))
    for endpoint_id, endpoint in misplaced:
        localhost_by_id.pop(endpoint_id, None)
        lan_by_id[endpoint_id] = endpoint

    provider_enabled = data.get("provider_enabled") or {}
    migrated_provider_enabled: dict[str, bool] = {}
    if isinstance(provider_enabled, dict):
        for provider_name, enabled in provider_enabled.items():
            mapped_name = aliases.get(str(provider_name), str(provider_name))
            endpoint = localhost_by_id.get(mapped_name) or lan_by_id.get(mapped_name)
            if endpoint is not None:
                endpoint["enabled"] = bool(enabled)
            else:
                migrated_provider_enabled[mapped_name] = bool(enabled)

    active_models: dict[str, str] = {}
    for provider_name, model_id in (data.get("active_models") or {}).items():
        mapped_name = aliases.get(str(provider_name), str(provider_name))
        if mapped_name not in active_models or not active_models[mapped_name]:
            active_models[mapped_name] = model_id

    benchmark_results: list[dict[str, Any]] = []
    for result in data.get("benchmark_results") or []:
        if not isinstance(result, dict):
            continue
        migrated = dict(result)
        provider_name = str(migrated.get("provider") or "")
        if provider_name:
            migrated["provider"] = aliases.get(provider_name, provider_name)
        benchmark_results.append(migrated)

    migrated_settings = {
        **{k: v for k, v in data.items() if k not in {
            "local_endpoints",
            "homelab_endpoints",
            "localhost_endpoints",
            "lan_endpoints",
            "provider_enabled",
            "active_models",
            "benchmark_results",
            "discovered_models",
        }},
        "provider_enabled": migrated_provider_enabled,
        "active_models": active_models,
        "benchmark_results": benchmark_results,
        "discovered_models": _migrate_discovered_models(data.get("discovered_models"), aliases),
    }

    localhost_keys_present = (
        "local_endpoints" in data
        or "localhost_endpoints" in data
        or bool(localhost_by_id)
    )
    lan_keys_present = (
        "local_endpoints" in data
        or "homelab_endpoints" in data
        or "lan_endpoints" in data
        or bool(lan_by_id)
    )

    if localhost_keys_present:
        migrated_settings["localhost_endpoints"] = list(localhost_by_id.values())
    if lan_keys_present:
        migrated_settings["lan_endpoints"] = list(lan_by_id.values())

    return migrated_settings


def _persist_if_possible(settings: ProtoNeoSettings) -> None:
    try:
        save_settings(settings)
    except Exception as exc:
        logger.warning("Failed to persist settings normalization: %s", exc)


def load_settings() -> ProtoNeoSettings:
    """Load settings from disk, migrating old schemas transparently.

    Never auto-persists. Settings are only written when explicitly
    saved through save_settings() or update_settings().
    """
    if not _SETTINGS_FILE.exists():
        return ProtoNeoSettings()

    try:
        raw_data = json.loads(_SETTINGS_FILE.read_text())
        migrated_data = _migrate_settings_data(raw_data)
        return ProtoNeoSettings.model_validate(migrated_data)
    except Exception as e:
        logger.warning("Failed to load settings: %s", e)
        return ProtoNeoSettings()


def save_settings(settings: ProtoNeoSettings) -> None:
    """Persist settings to disk."""
    _SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    _SETTINGS_FILE.write_text(settings.model_dump_json(indent=2))
    logger.info("Settings saved to %s", _SETTINGS_FILE)


def update_settings(patch: dict[str, Any]) -> ProtoNeoSettings:
    """Load, merge partial update, save, and return."""
    current = load_settings()
    merged = current.model_dump()
    merged.update(patch)
    updated = ProtoNeoSettings.model_validate(_migrate_settings_data(merged))
    save_settings(updated)
    return updated


def all_configured_endpoints(settings: ProtoNeoSettings | None = None) -> list[LocalEndpoint]:
    """Return all configured localhost and LAN endpoints."""
    active_settings = settings or load_settings()
    return [*active_settings.localhost_endpoints, *active_settings.lan_endpoints]


def endpoint_map(settings: ProtoNeoSettings | None = None) -> dict[str, LocalEndpoint]:
    """Return configured localhost/LAN endpoints keyed by endpoint id."""
    return {
        endpoint.id: endpoint
        for endpoint in all_configured_endpoints(settings)
    }


def endpoint_alias_map(settings: ProtoNeoSettings | None = None) -> dict[str, str]:
    """Return best-effort aliases for migrated endpoint ids."""
    aliases = dict(_LEGACY_PROVIDER_IDS)
    for endpoint in all_configured_endpoints(settings):
        aliases[endpoint.id] = endpoint.id
        aliases.setdefault(_slugify(endpoint.display_name), endpoint.id)
    return aliases


def provider_is_enabled(provider_name: str, settings: ProtoNeoSettings | None = None) -> bool:
    """Check whether a provider is enabled for routing/UI use."""
    active_settings = settings or load_settings()
    endpoint_id = endpoint_alias_map(active_settings).get(provider_name, provider_name)
    configured_endpoint = endpoint_map(active_settings).get(endpoint_id)
    if configured_endpoint is not None:
        return configured_endpoint.enabled
    return active_settings.provider_enabled.get(endpoint_id, True)


def active_model_assignments(
    settings: ProtoNeoSettings | None = None,
    provider_registry=None,
) -> dict[str, dict[str, str]]:
    """Return ready-to-use routing assignments for enabled active models."""
    active_settings = settings or load_settings()

    from .providers.registry import get_provider_registry
    from .registry import CapabilityRegistry

    registry = CapabilityRegistry.from_settings(active_settings)
    oauth_registry = provider_registry or get_provider_registry()
    endpoints = endpoint_map(active_settings)

    assignments: dict[str, dict[str, str]] = {}
    for provider, model_id in active_settings.active_models.items():
        if not model_id or not provider_is_enabled(provider, active_settings):
            continue

        registry_info = registry.get(f"{provider}/{model_id}")
        api_key_source = "local"
        if provider not in endpoints:
            if provider == "openrouter":
                api_key_source = "env" if os.getenv("OPENROUTER_API_KEY") else "config"
            else:
                credential_info = oauth_registry.resolve_credential_info(provider)
                api_key_source = credential_info.get("api_key_source", "none")

        assignments[provider] = {
            "model_id": model_id,
            "litellm_model": registry_info.effective_model,
            "api_base": registry_info.api_base or "",
            "api_key_source": api_key_source,
        }

    return assignments
