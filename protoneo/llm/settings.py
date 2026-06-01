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
    "lan-mini": {
        "display_name": "Mini",
        "location": _LAN,
        "type": "openai",
    },
    "lan-dynamo": {
        "display_name": "Dynamo",
        "location": _LAN,
        "type": "openai",
    },
}

_KNOWN_HOSTS = {
    "localhost": "localhost-lmstudio",
    "127.0.0.1": "localhost-lmstudio",
    "::1": "localhost-lmstudio",
    "192.168.86.143": "lan-dynamo",
}

class LocalEndpoint(BaseModel):
    """A configured LLM service endpoint."""

    id: str
    display_name: str
    url: str
    type: str = "openai"  # "openai" or "ollama"
    enabled: bool = True
    location: str = _LOCALHOST  # "localhost" or "lan"


class VlmEndpoint(BaseModel):
    """VLM endpoint for figure description during PDF parsing."""

    enabled: bool = False
    url: str = ""
    model: str = ""
    prompt: str = (
        "Describe the figure or table for a scientific reviewer in 6 concise sentences. "
        "Include chart/table type, axes or columns, compared methods, key numeric trends, "
        "and any result needed to verify the manuscript. "
        "Output plain text only. Do not include reasoning, scratchpad, markdown, or speculation."
    )
    temperature: float = 0.1
    top_p: float = 0.9
    timeout: float = 120.0
    concurrency: int = 1


def _default_localhost_endpoints() -> list[LocalEndpoint]:
    return [
        LocalEndpoint(
            id="localhost-lmstudio",
            display_name="LM Studio",
            url="http://localhost:1234/v1",
            type="openai",
            location=_LOCALHOST,
            enabled=False,
        ),
        LocalEndpoint(
            id="localhost-ollama",
            display_name="Ollama",
            url="http://localhost:11434",
            type="ollama",
            location=_LOCALHOST,
            enabled=False,
        ),
    ]


def _default_lan_endpoints() -> list[LocalEndpoint]:
    return [
        LocalEndpoint(
            id="lan-mini",
            display_name="Mini",
            url="http://192.168.86.141:8080/v1",
            type="openai",
            location=_LAN,
            enabled=True,
        ),
        LocalEndpoint(
            id="lan-dynamo",
            display_name="Dynamo",
            url="http://192.168.86.143:1234/v1",
            type="openai",
            location=_LAN,
            enabled=True,
        ),
    ]


class ModelPreset(BaseModel):
    """Named model assignment preset.

    Maps role IDs and graph step IDs to provider-prefixed model IDs.
    Graph steps: ontology, extraction, coref, verification.
    Review roles: defined by conference profile (technical, novelty, etc.).
    """

    name: str
    description: str = ""
    assignments: dict[str, str] = Field(default_factory=dict)


class ProtoNeoSettings(BaseModel):
    """Root settings object persisted to disk.

    This is ProtoNeo runtime configuration, not per-review settings.
    Per-review settings (role assignments, tool toggles) live in the
    review session config and are set in PanelHome.
    """

    localhost_endpoints: list[LocalEndpoint] = Field(default_factory=_default_localhost_endpoints)
    lan_endpoints: list[LocalEndpoint] = Field(default_factory=_default_lan_endpoints)
    openrouter_free_only: bool = True
    provider_enabled: dict[str, bool] = Field(default_factory=dict)
    active_models: dict[str, str] = Field(default_factory=dict)
    active_model_options: dict[str, dict[str, Any]] = Field(default_factory=dict)
    presets: list[ModelPreset] = Field(default_factory=list)
    active_preset: str = Field(default="", description="Name of the currently active preset")
    vlm_endpoint: VlmEndpoint = Field(default_factory=VlmEndpoint)
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
    raw_label = raw_display_name or raw_name
    default_label = str(defaults.get("display_name") or "")
    if default_label and _slugify(raw_label) in {
        _slugify(default_label),
        endpoint_id.removeprefix("lan-").removeprefix("localhost-"),
    }:
        display_name = default_label
    else:
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

    active_model_options: dict[str, dict[str, Any]] = {}
    for provider_name, options in (data.get("active_model_options") or {}).items():
        if not isinstance(options, dict):
            continue
        mapped_name = aliases.get(str(provider_name), str(provider_name))
        active_model_options[mapped_name] = dict(options)

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
            "active_model_options",
            "benchmark_results",
            "discovered_models",
        }},
        "provider_enabled": migrated_provider_enabled,
        "active_models": active_models,
        "active_model_options": active_model_options,
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


def _merge_endpoint_patch(
    current: list[LocalEndpoint],
    patch_value: Any,
    default_location: str,
) -> list[dict[str, Any]]:
    """Merge endpoint patches by id so partial UI saves cannot erase providers."""
    current_by_id = {endpoint.id: endpoint.model_dump() for endpoint in current}
    if not isinstance(patch_value, list):
        return list(current_by_id.values())

    order = [endpoint.id for endpoint in current]
    for raw_endpoint in patch_value:
        normalized = _normalize_endpoint_payload(raw_endpoint, default_location)
        if normalized is None:
            continue
        endpoint_id = normalized["id"]
        current_by_id[endpoint_id] = {**current_by_id.get(endpoint_id, {}), **normalized}
        if endpoint_id not in order:
            order.append(endpoint_id)

    return [current_by_id[endpoint_id] for endpoint_id in order if endpoint_id in current_by_id]


def update_settings(patch: dict[str, Any]) -> ProtoNeoSettings:
    """Load, merge partial update, save, and return."""
    current = load_settings()
    patch = dict(patch)
    # The Settings UI sends the full runtime settings shape, and a page-level
    # save can race before endpoint arrays have loaded. Do not let an empty
    # client-side placeholder wipe configured local/LAN providers.
    for endpoint_key in ("localhost_endpoints", "lan_endpoints"):
        if patch.get(endpoint_key) == [] and getattr(current, endpoint_key):
            patch.pop(endpoint_key, None)
    merged = current.model_dump()

    for endpoint_key, default_location in (
        ("localhost_endpoints", _LOCALHOST),
        ("lan_endpoints", _LAN),
    ):
        if endpoint_key in patch:
            merged[endpoint_key] = _merge_endpoint_patch(
                getattr(current, endpoint_key),
                patch.pop(endpoint_key),
                default_location,
            )

    for dict_key in ("provider_enabled", "active_models", "active_model_options"):
        if isinstance(patch.get(dict_key), dict):
            merged[dict_key] = {**merged.get(dict_key, {}), **patch.pop(dict_key)}

    merged.update(patch)
    updated = ProtoNeoSettings.model_validate(_migrate_settings_data(merged))
    save_settings(updated)
    return updated


def build_vlm_config(settings: ProtoNeoSettings | None = None) -> dict[str, Any] | None:
    """Build a vlm_config dict from settings, or None if no VLM endpoint is configured."""
    s = settings or load_settings()
    if not s.vlm_endpoint.enabled or not s.vlm_endpoint.url:
        return None
    return s.vlm_endpoint.model_dump()


def vlm_status(settings: ProtoNeoSettings | None = None) -> dict[str, Any]:
    """Report VLM configuration and reachability for preflight/UX."""
    active_settings = settings or load_settings()
    endpoint = active_settings.vlm_endpoint
    out = {
        "configured": bool(endpoint.enabled and endpoint.url),
        "url": endpoint.url,
        "model": endpoint.model,
        "reachable": False,
        "error": "",
    }
    if not out["configured"]:
        out["error"] = "VLM endpoint disabled or unset"
        return out
    try:
        import httpx

        base = endpoint.url.split("/chat/completions")[0].rstrip("/")
        response = httpx.get(f"{base}/models", timeout=4.0)
        out["reachable"] = response.status_code == 200
        if response.status_code != 200:
            out["error"] = f"HTTP {response.status_code}"
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


# ── Built-in presets ──────────────────────────────────────
#
# These ship with ProtoNeo and are always available. Users can
# override them or add custom presets via settings.json.
# Model IDs use the provider-prefixed format (e.g. "lan-mini/model-name").

_BUILTIN_PRESETS: list[ModelPreset] = [
    ModelPreset(
        name="mini-nemotron-omni-graph",
        description="Mini Nemotron Omni for graph building and visual evidence extraction",
        assignments={
            "ontology": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "extraction": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "coref": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "verification": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "artifact": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
        },
    ),
    ModelPreset(
        name="dynamo-nemotron-omni-graph",
        description="Dynamo Nemotron Omni for graph building and visual evidence extraction",
        assignments={
            "ontology": "lan-dynamo/nvidia-nemotron-3-nano-omni-30b-a3b-reasoning",
            "extraction": "lan-dynamo/nvidia-nemotron-3-nano-omni-30b-a3b-reasoning",
            "coref": "lan-dynamo/nvidia-nemotron-3-nano-omni-30b-a3b-reasoning",
            "verification": "lan-dynamo/nvidia-nemotron-3-nano-omni-30b-a3b-reasoning",
            "artifact": "lan-dynamo/nvidia-nemotron-3-nano-omni-30b-a3b-reasoning",
        },
    ),
    ModelPreset(
        name="dynamo-gemma-graph",
        description="Dynamo Gemma for structured semantic graph building; saved visual evidence remains separately attributed",
        assignments={
            "ontology": "lan-dynamo/gemma-4-31b-it-nvfp4-turbo",
            "extraction": "lan-dynamo/gemma-4-31b-it-nvfp4-turbo",
            "coref": "lan-dynamo/gemma-4-31b-it-nvfp4-turbo",
            "verification": "lan-dynamo/gemma-4-31b-it-nvfp4-turbo",
            "artifact": "lan-dynamo/nvidia-nemotron-3-nano-omni-30b-a3b-reasoning",
        },
    ),
    ModelPreset(
        name="openai-only",
        description="Mini graph pipeline, GPT-5.5 reviewers, GPT-5.5 meta",
        assignments={
            "ontology": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "extraction": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "coref": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "verification": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "technical": "openai/gpt-5.5-mini",
            "systems": "openai/gpt-5.5-mini",
            "novelty": "openai/gpt-5.5-mini",
            "clarity": "openai/gpt-5.5-mini",
            "skeptic": "openai/gpt-5.5-mini",
            "meta_reviewer": "openai/gpt-5.5",
            "meta": "openai/gpt-5.5",
        },
    ),
    ModelPreset(
        name="nemotron-omni-local",
        description="Mini Nemotron Omni for graph and local review dry-runs",
        assignments={
            "ontology": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "extraction": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "coref": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "verification": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "technical": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "systems": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "clarity": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "novelty": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "skeptic": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "artifact": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "meta_reviewer": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "meta": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
        },
    ),
    ModelPreset(
        name="hpdc26-openai",
        description="HPDC '26 reviews: Mini graph pipeline, GPT-5.5 analytical roles, GPT-5.5-mini clarity",
        assignments={
            "ontology": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "extraction": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "coref": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "verification": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "technical": "openai/gpt-5.5",
            "novelty": "openai/gpt-5.5",
            "skeptic": "openai/gpt-5.5",
            "clarity": "openai/gpt-5.5-mini",
            "meta_reviewer": "openai/gpt-5.5",
            "meta": "openai/gpt-5.5",
        },
    ),
    ModelPreset(
        name="full-local",
        description="Local graph and review dry-run",
        assignments={
            "ontology": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "extraction": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "coref": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "verification": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "technical": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "systems": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "novelty": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "clarity": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "skeptic": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "artifact": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "meta_reviewer": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
            "meta": "lan-mini/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-UD-Q4_K_M",
        },
    ),
]


def get_all_presets(settings: ProtoNeoSettings | None = None) -> list[ModelPreset]:
    """Return built-in presets plus any user-defined ones from settings."""
    s = settings or load_settings()
    builtin_names = {p.name for p in _BUILTIN_PRESETS}
    user_presets = [
        p
        for p in s.presets
        if p.name not in builtin_names
    ]
    return _BUILTIN_PRESETS + user_presets


def resolve_preset(name: str, settings: ProtoNeoSettings | None = None) -> ModelPreset | None:
    """Look up a preset by name."""
    for p in get_all_presets(settings):
        if p.name == name:
            return p
    return None


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


def active_model_is_routable(settings: ProtoNeoSettings, provider: str, model_id: str) -> bool:
    """Return whether a selected model is allowed into runtime routing."""
    for entry in settings.discovered_models.get(provider, []) or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id") or entry.get("model_id") or "") != model_id:
            continue
        if str(entry.get("availability") or "").strip() == "unsupported":
            return False
        return True
    return True


def active_model_assignments(
    settings: ProtoNeoSettings | None = None,
    provider_registry=None,
) -> dict[str, dict[str, Any]]:
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
        if not active_model_is_routable(active_settings, provider, model_id):
            continue

        registry_info = registry.get(f"{provider}/{model_id}")
        model_options = dict(active_settings.active_model_options.get(provider) or {})
        api_key_source = "local"
        if provider not in endpoints:
            if provider == "openrouter":
                api_key_source = "env" if os.getenv("OPENROUTER_API_KEY") else "none"
            else:
                credential_info = oauth_registry.resolve_credential_info(provider)
                api_key_source = credential_info.get("api_key_source", "none")

        assignments[provider] = {
            "model_id": model_id,
            "litellm_model": registry_info.effective_model,
            "api_base": registry_info.api_base or "",
            "api_key_source": api_key_source,
            "capabilities": sorted(c.value for c in registry_info.capabilities),
            "quirks": sorted(q.value for q in registry_info.quirks),
            "latency_class": registry_info.latency_class.value,
            "structured_output": registry_info.structured_output.value,
            "runtime_location": registry_info.runtime_location,
            "options": model_options,
            "reasoning_effort": model_options.get("reasoning_effort", ""),
        }

    return assignments
