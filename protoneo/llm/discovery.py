"""Dynamic model discovery and benchmarking.

Discovers models from all connected providers at runtime.
Nothing is hardcoded. Local services (LM Studio, Ollama) are
auto-detected. Cloud provider models come from their APIs.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

logger = logging.getLogger("protoneo.llm.discovery")


def _is_oauth_token(provider: str, api_key: str, credential_info: dict[str, Any] | None = None) -> bool:
    """Best-effort detection for subscription OAuth tokens."""
    token_type = (credential_info or {}).get("token_type", "")
    source = (credential_info or {}).get("api_key_source", "")
    if token_type == "oauth" or source == "oauth":
        return True
    if provider == "anthropic":
        return api_key.startswith("sk-ant-oat")
    return False


async def _get_json(url: str, headers: dict | None = None, timeout: float = 3.0) -> dict | list | None:
    """GET a URL and parse JSON. Returns None on any failure."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(url, headers=headers or {})
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug("GET %s failed: %s", url, e)
    return None


async def _post_json(url: str, body: dict | None = None, headers: dict | None = None, timeout: float = 10.0) -> dict | None:
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(url, json=body or {}, headers=headers or {})
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.debug("POST %s failed: %s", url, e)
    return None


# ── Local service detection ────────────────────────────────

def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _service_root(base_url: str) -> str:
    """Return the service root for OpenAI-compatible endpoints.

    LM Studio exposes management metadata under /api/v*/models while its
    OpenAI-compatible API lives under /v1.
    """
    parsed = urlparse(base_url.rstrip("/"))
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        path = path[:-3] or ""
    return urlunparse(parsed._replace(path=path, params="", query="", fragment="")).rstrip("/")


def _value_after_arg(args: list[Any], names: set[str]) -> str | None:
    for i, arg in enumerate(args):
        if str(arg) in names and i + 1 < len(args):
            return str(args[i + 1])
    return None


def _context_from_preset(preset: str) -> int | None:
    for key in ("ctx-size", "ctx_size", "n_ctx", "n-ctx", "context_length", "max_context_length"):
        match = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*([0-9]+)\s*$", preset)
        if match:
            parsed = _coerce_positive_int(match.group(1))
            if parsed:
                return parsed
    return None


def _extract_context_length(model: dict[str, Any]) -> int | None:
    for key in (
        "context_length",
        "max_context_length",
        "context_window",
        "max_context",
        "ctx_size",
        "n_ctx",
    ):
        parsed = _coerce_positive_int(model.get(key))
        if parsed:
            return parsed

    status = model.get("status")
    if isinstance(status, dict):
        args = status.get("args")
        if isinstance(args, list):
            parsed = _coerce_positive_int(_value_after_arg(
                args,
                {"--ctx-size", "--ctx_size", "--n-ctx", "--n_ctx", "-c"},
            ))
            if parsed:
                return parsed
        preset = status.get("preset")
        if isinstance(preset, str):
            parsed = _context_from_preset(preset)
            if parsed:
                return parsed

    return None


def _normalize_lmstudio_metadata(raw_model: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    model_id = str(raw_model.get("id") or raw_model.get("key") or "").strip()
    if not model_id:
        return None

    metadata: dict[str, Any] = {
        "metadata_source": "lmstudio",
    }
    display_name = raw_model.get("display_name") or raw_model.get("name")
    if display_name:
        metadata["display_name"] = display_name
    context_length = _extract_context_length(raw_model)
    if context_length:
        metadata["context_length"] = context_length
        metadata["context_source"] = "lmstudio"
    state = raw_model.get("state")
    if isinstance(state, str):
        metadata["loaded"] = state == "loaded"
    loaded_instances = raw_model.get("loaded_instances")
    if isinstance(loaded_instances, list):
        metadata["loaded"] = bool(loaded_instances)
    capabilities = raw_model.get("capabilities")
    if isinstance(capabilities, dict):
        metadata["tools"] = bool(capabilities.get("trained_for_tool_use"))
        metadata["vision"] = bool(capabilities.get("vision"))
    elif isinstance(capabilities, list):
        metadata["tools"] = "tool_use" in capabilities or "tools" in capabilities
        metadata["vision"] = "vision" in capabilities
    quantization = raw_model.get("quantization")
    if isinstance(quantization, dict):
        metadata["quantization"] = quantization.get("name") or ""
    elif quantization:
        metadata["quantization"] = str(quantization)
    for source_key, target_key in (
        ("arch", "architecture"),
        ("architecture", "architecture"),
        ("publisher", "publisher"),
        ("params_string", "parameter_size"),
        ("format", "format"),
        ("size_bytes", "size_bytes"),
    ):
        if raw_model.get(source_key) is not None:
            metadata[target_key] = raw_model[source_key]
    return model_id, metadata


async def fetch_lmstudio_metadata(base_url: str) -> dict[str, dict[str, Any]]:
    """Fetch richer LM Studio model metadata when the endpoint supports it."""
    root = _service_root(base_url)
    metadata_by_id: dict[str, dict[str, Any]] = {}
    if not root:
        return metadata_by_id

    for path in ("/api/v1/models", "/api/v0/models"):
        data = await _get_json(f"{root}{path}", timeout=5.0)
        if not isinstance(data, dict):
            continue
        raw_models = data.get("models") or data.get("data") or []
        if not isinstance(raw_models, list):
            continue
        for raw_model in raw_models:
            if not isinstance(raw_model, dict):
                continue
            normalized = _normalize_lmstudio_metadata(raw_model)
            if normalized is None:
                continue
            model_id, metadata = normalized
            metadata_by_id[model_id] = {**metadata_by_id.get(model_id, {}), **metadata}
    return metadata_by_id


async def probe_openai_endpoint(
    endpoint_id: str,
    display_name: str,
    base_url: str,
    location: str,
) -> dict[str, Any]:
    """Probe an OpenAI-compatible endpoint (LM Studio, llama-server, vLLM, etc.).

    Extracts real model configuration from the endpoint response.
    llama-server exposes ctx-size, temperature, status (loaded/unloaded)
    in the model listing. LM Studio returns simpler metadata.
    """
    url = f"{base_url.rstrip('/')}/models"
    data = await _get_json(url, headers={"Authorization": "Bearer none"})
    if data is None:
        return {
            "id": endpoint_id,
            "name": endpoint_id,
            "display_name": display_name,
            "location": location,
            "url": base_url,
            "type": "openai",
            "online": False,
            "models": [],
            "loaded_model": None,
        }

    models_raw = data.get("data", []) if isinstance(data, dict) else data
    lmstudio_metadata = await fetch_lmstudio_metadata(base_url)
    models = []
    loaded_model = None
    for m in models_raw:
        if not isinstance(m, dict):
            continue
        mid = m.get("id", "")
        if not mid:
            continue

        entry = {
            "id": mid,
            "owned_by": m.get("owned_by", ""),
            "source": endpoint_id,
            "provider_type": "local",
        }
        context_length = _extract_context_length(m)
        if context_length:
            entry["context_length"] = context_length
            entry["context_source"] = "openai_models"

        # llama-server exposes status and launch args per model
        status_info = m.get("status")
        if isinstance(status_info, dict):
            entry["loaded"] = status_info.get("value") == "loaded"
            if entry["loaded"]:
                loaded_model = mid

            # Parse server args for real config (ctx-size, temperature, etc.)
            args = status_info.get("args", [])
            if isinstance(args, list):
                for i, arg in enumerate(args):
                    if arg == "--temperature" and i + 1 < len(args):
                        try:
                            entry["temperature"] = float(args[i + 1])
                        except (ValueError, TypeError):
                            pass
                    elif arg == "--model" and i + 1 < len(args):
                        entry["model_path"] = args[i + 1]
                    elif arg == "--flash-attn":
                        entry["flash_attention"] = True
        else:
            # LM Studio: single loaded model if only one in list
            if len(models_raw) == 1:
                loaded_model = mid
                entry["loaded"] = True

        if mid in lmstudio_metadata:
            lmstudio_entry = lmstudio_metadata[mid]
            entry = {**entry, **lmstudio_entry}
            if lmstudio_entry.get("loaded"):
                loaded_model = mid

        models.append(entry)

    return {
        "id": endpoint_id,
        "name": endpoint_id,
        "display_name": display_name,
        "location": location,
        "url": base_url,
        "type": "openai",
        "online": True,
        "models": models,
        "loaded_model": loaded_model,
    }


async def probe_ollama_endpoint(
    endpoint_id: str,
    display_name: str,
    base_url: str,
    location: str,
) -> dict[str, Any]:
    """Probe an Ollama endpoint.

    Uses /api/tags for downloaded models and /api/ps for currently loaded models.
    """
    url = f"{base_url.rstrip('/')}/api/tags"
    data = await _get_json(url)
    if data is None:
        return {
            "id": endpoint_id,
            "name": endpoint_id,
            "display_name": display_name,
            "location": location,
            "url": base_url,
            "type": "ollama",
            "online": False,
            "models": [],
            "loaded_models": [],
            "loaded_model": None,
        }

    models = []
    for m in data.get("models", []):
        mid = m.get("name", "")
        if mid:
            size_gb = round(m.get("size", 0) / 1e9, 1) if m.get("size") else None
            models.append({
                "id": mid,
                "size_gb": size_gb,
                "parameter_size": m.get("details", {}).get("parameter_size", ""),
                "quantization": m.get("details", {}).get("quantization_level", ""),
                "family": m.get("details", {}).get("family", ""),
                "source": endpoint_id,
                "provider_type": "local",
            })

    # Check what's currently loaded in memory
    loaded_models = []
    ps_data = await _get_json(f"{base_url.rstrip('/')}/api/ps")
    if ps_data and isinstance(ps_data, dict):
        for m in ps_data.get("models", []):
            loaded_models.append(m.get("name", ""))

    loaded_set = set(loaded_models)
    for model in models:
        model["loaded"] = model["id"] in loaded_set

    return {
        "id": endpoint_id,
        "name": endpoint_id,
        "display_name": display_name,
        "location": location,
        "url": base_url,
        "type": "ollama",
        "online": True,
        "models": models,
        "loaded_models": loaded_models,
        "loaded_model": loaded_models[0] if loaded_models else None,
    }


_MODEL_SUGGESTIONS = (
    "Recommended: Qwen3.5 family (unsloth/Qwen3.5-*-GGUF on HuggingFace). "
    "24GB VRAM: `ollama pull qwen3.5:35b` (35B-A3B MoE, 3B active, Q4_K_M ~20GB). "
    "16GB VRAM: `ollama pull qwen3.5:9b` (9B dense, Q4_K_M ~6GB). "
    "8GB VRAM: `ollama pull qwen3.5:4b` (4B dense, Q4_K_M ~3GB). "
    "Or search 'qwen3.5' in LM Studio to download GGUF variants from unsloth."
)


async def discover_local(endpoints: list[dict]) -> list[dict[str, Any]]:
    """Discover models from configured local endpoints."""
    tasks = []
    for ep in endpoints:
        endpoint_id = ep.get("id") or ep.get("name") or "unknown"
        display_name = ep.get("display_name") or endpoint_id
        url = ep.get("url", "")
        ep_type = ep.get("type", "openai")
        location = ep.get("location", "localhost")
        if not ep.get("enabled", True):
            continue
        if ep_type == "ollama":
            tasks.append(probe_ollama_endpoint(endpoint_id, display_name, url, location))
        else:
            tasks.append(probe_openai_endpoint(endpoint_id, display_name, url, location))

    if not tasks:
        return []
    results = list(await asyncio.gather(*tasks))

    # Add nudge if no models found on any online endpoint
    for r in results:
        if r.get("online") and not r.get("models"):
            r["nudge"] = (
                f"Service is running at {r['url']} but no models are loaded. "
                + _MODEL_SUGGESTIONS
            )

    return results


# ── Cloud provider discovery ──────────────────────────────

def _merge_model_lists(*model_lists: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge provider model lists by id while preserving richer metadata."""
    merged: dict[str, dict[str, Any]] = {}
    for models in model_lists:
        for model in models or []:
            if not isinstance(model, dict):
                continue
            model_id = str(model.get("id") or "").strip()
            if not model_id:
                continue
            existing = merged.get(model_id, {})
            merged[model_id] = {**existing, **model}
    return list(merged.values())


def _with_discovery_source(models: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    return [{**model, "discovery_source": source} for model in models]


async def discover_openrouter(api_key: str, free_only: bool = False) -> dict[str, Any]:
    """Discover OpenRouter models. Optionally filter to free tier only."""
    data = await _get_json(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15.0,
    )
    if data is None:
        return {"provider": "openrouter", "online": False, "models": [], "error": "API unreachable"}

    models = []
    for m in data.get("data", []):
        pricing = m.get("pricing", {})
        prompt_cost = float(pricing.get("prompt", "1") or "1")
        completion_cost = float(pricing.get("completion", "1") or "1")
        is_free = prompt_cost == 0 and completion_cost == 0

        if free_only and not is_free:
            continue

        models.append({
            "id": m.get("id", ""),
            "name": m.get("name", ""),
            "context_length": m.get("context_length", 0),
            "is_free": is_free,
            "cost_prompt": prompt_cost,
            "cost_completion": completion_cost,
            "source": "openrouter",
            "provider_type": "api",
            "top_provider": m.get("top_provider", {}).get("is_moderated", False),
        })

    return {"provider": "openrouter", "online": True, "models": models, "total_available": len(data.get("data", []))}


async def discover_anthropic(
    api_key: str,
    credential_info: dict[str, Any] | None = None,
    models_dev_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Discover Anthropic models.

    OAuth tokens (Claude Max subscription) get the static subscription catalog
    matching Claude Code's /model list. API keys query /v1/models live.
    """
    is_oauth = _is_oauth_token("anthropic", api_key, credential_info)

    if is_oauth:
        from .catalogs import ANTHROPIC_SUBSCRIPTION_MODELS
        return {
            "provider": "anthropic",
            "online": True,
            "models": list(ANTHROPIC_SUBSCRIPTION_MODELS),
            "credential_type": "oauth",
        }

    # Standard API key: query live
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    data = await _get_json("https://api.anthropic.com/v1/models", headers=headers, timeout=10.0)

    models = []
    if data and isinstance(data, dict):
        for m in data.get("data", []):
            mid = m.get("id", "")
            models.append({
                "id": mid,
                "name": m.get("display_name", mid),
                "context_length": m.get("context_window", 200000),
                "source": "anthropic",
                "provider_type": "api",
            })

    return {
        "provider": "anthropic",
        "online": data is not None,
        "models": models,
        "credential_type": "env",
        **({"nudge": "Connected but no models found."} if not models else {}),
    }


async def discover_openai(
    api_key: str,
    credential_info: dict[str, Any] | None = None,
    models_dev_data: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Discover OpenAI models.

    OAuth tokens (ChatGPT subscription) use the runtime catalog because
    the subscription endpoint cannot enumerate models via /v1/models. The
    bundled catalog is only a seed/fallback so newer ChatGPT subscription
    models can appear after Refresh All without a code release.
    API keys query /v1/models live.
    """
    is_oauth = _is_oauth_token("openai", api_key, credential_info)

    if is_oauth:
        from .catalogs import OPENAI_SUBSCRIPTION_MODELS
        from .models_dev import discover_provider_models, parse_provider_models

        catalog_models: list[dict[str, Any]] = []
        if models_dev_data is not None:
            catalog_models = parse_provider_models(models_dev_data, "openai")
        else:
            catalog_models = await discover_provider_models(
                "openai",
                force_refresh=force_refresh,
            )
        models = _merge_model_lists(
            _with_discovery_source(list(OPENAI_SUBSCRIPTION_MODELS), "fallback_seed"),
            _with_discovery_source(catalog_models, "live_catalog"),
        )
        return {
            "provider": "openai",
            "online": bool(catalog_models),
            "models": models,
            "credential_type": "oauth",
            "catalog_source": "models.dev+seed" if catalog_models else "seed",
            "using_cache": not bool(catalog_models),
            **({"nudge": "Using bundled OpenAI/Codex fallback catalog; live catalog is unavailable."} if not catalog_models else {}),
        }

    # Standard API key: query live
    data = await _get_json(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10.0,
    )

    if data is None:
        return {
            "provider": "openai",
            "online": False,
            "models": [],
            "credential_type": "env",
            "nudge": "OpenAI API unreachable.",
        }

    chat_prefixes = ("gpt-4", "gpt-5", "gpt-3.5", "o1", "o3", "o4")
    models = []
    for m in data.get("data", []):
        mid = m.get("id", "")
        if any(mid.startswith(p) for p in chat_prefixes):
            models.append({
                "id": mid,
                "name": mid,
                "source": "openai",
                "provider_type": "api",
            })

    return {
        "provider": "openai",
        "online": True,
        "models": sorted(models, key=lambda x: x["id"]),
        "credential_type": "env",
    }


async def discover_all(
    localhost_endpoints: list[dict],
    lan_endpoints: list[dict],
    provider_credentials: dict[str, dict[str, Any]],
    openrouter_free_only: bool = True,
    cached_models: dict[str, list[dict[str, Any]]] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Discover models from all configured providers.

    Fetches the models.dev catalog once (cached 6h) and uses it to
    enrich subscription provider discovery. Local endpoints are probed
    directly. Nothing is hardcoded.
    """
    tasks = {}

    # Local (single machine)
    if localhost_endpoints:
        tasks["localhost"] = discover_local(localhost_endpoints)

    # Homelab (LAN)
    if lan_endpoints:
        tasks["lan"] = discover_local(lan_endpoints)

    # API-based
    openrouter_key = provider_credentials.get("openrouter", {}).get("api_key")
    if openrouter_key:
        tasks["openrouter"] = discover_openrouter(openrouter_key, free_only=openrouter_free_only)

    # Subscriptions discovered from the provider-authenticated runtime paths
    # DISABLED: Anthropic provider removed from ProtoNeo
    # anthropic = provider_credentials.get("anthropic", {})
    # if anthropic.get("api_key"):
    #     tasks["anthropic"] = discover_anthropic(anthropic["api_key"], anthropic)
    openai_creds = provider_credentials.get("openai", {})
    if openai_creds.get("api_key"):
        tasks["openai"] = discover_openai(
            openai_creds["api_key"],
            openai_creds,
            force_refresh=force_refresh,
        )

    results_list = await asyncio.gather(*tasks.values(), return_exceptions=True)

    results = {}
    for key, result in zip(tasks.keys(), results_list):
        if isinstance(result, Exception):
            results[key] = {"error": str(result)}
        else:
            results[key] = result

    if cached_models:
        for group_name in ("localhost", "lan"):
            nodes = results.get(group_name)
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                provider_id = str(node.get("id") or "")
                if not provider_id or node.get("online") or node.get("models"):
                    continue
                cached = cached_models.get(provider_id) or []
                if cached:
                    node["models"] = [
                        {**model, "source": provider_id, "discovery_source": "cache"}
                        for model in cached
                        if isinstance(model, dict)
                    ]
                    node["using_cache"] = True
                    node["nudge"] = "Live discovery failed; showing cached models."

        for provider_id, result in list(results.items()):
            if provider_id in {"localhost", "lan"}:
                continue
            if not isinstance(result, dict):
                continue
            if result.get("online") and result.get("models"):
                continue
            cached = cached_models.get(provider_id) or []
            if cached:
                results[provider_id] = {
                    **result,
                    "provider": provider_id,
                    "online": False,
                    "models": [
                        {**model, "source": provider_id, "discovery_source": "cache"}
                        for model in cached
                        if isinstance(model, dict)
                    ],
                    "using_cache": True,
                    "nudge": "Live discovery failed; showing cached models.",
                }

    return results


# ── Benchmark ──────────────────────────────────────────────

_BENCHMARK_ABSTRACT = """\
We present ScaleSort, a distributed sorting algorithm for exascale systems \
that achieves near-linear speedup up to 65,536 nodes. ScaleSort combines \
adaptive sampling with hierarchical merging to minimize inter-node \
communication. Our evaluation on Summit and Frontier supercomputers shows \
2.3x throughput improvement over the state-of-the-art RadixSort-MPI, while \
reducing network traffic by 41%. We provide theoretical analysis proving \
O(n/p log p) communication complexity and validate our claims with weak \
and strong scaling experiments across three architectures.\
"""

_BENCHMARK_PROMPT = """\
You are a peer reviewer for a top-tier HPC conference. Review this abstract and return a JSON object with exactly this structure:

{"summary": "2-sentence summary", "strengths": ["strength 1", "strength 2", "strength 3"], "weaknesses": ["weakness 1", "weakness 2", "weakness 3"], "overall_merit": {"score": <1-5>, "label": "<reject|weak_reject|borderline|weak_accept|accept>"}, "confidence": {"score": <1-5>, "label": "<low|medium|high|expert>"}}

Abstract:
""" + _BENCHMARK_ABSTRACT + "\n\nReturn ONLY the JSON object, no other text."


async def benchmark_model(
    model_id: str,
    llm_client: "LLMClient",
    session_id: str = "benchmark",
    provider: str = "",
    api_base: str = "",
    litellm_prefix: str = "",
) -> dict[str, Any]:
    """Run a mini review benchmark on a single model.

    For local/homelab models, the caller must provide api_base and
    litellm_prefix so we can route correctly. For cloud models,
    the LLMClient's registry handles routing.
    """
    import re

    result = {
        "model_id": model_id,
        "provider": provider,
        "status": "pending",
        "latency_seconds": 0,
        "tokens_per_second": 0,
        "output_valid_json": False,
        "output_complete": False,
        "output_has_scores": False,
        "score": 0,
        "protoneo_class": "",
        "error": None,
    }

    # Build the correct LiteLLM model string and routing kwargs
    extra_kwargs = {}

    # Always apply the provider prefix for LiteLLM routing
    if litellm_prefix:
        effective_model = f"{litellm_prefix}{model_id}"
    else:
        effective_model = model_id

    # Set endpoint URL for local/homelab providers
    if api_base:
        extra_kwargs["api_base"] = api_base
        extra_kwargs["api_key"] = "none"  # local endpoints accept any key

    # Warm-up call to ensure model is loaded in VRAM
    try:
        await llm_client.complete(
            model=effective_model,
            messages=[{"role": "user", "content": "Hello"}],
            session_id=session_id,
            max_tokens=5,
            **extra_kwargs,
        )
    except Exception:
        pass  # Warm-up failure is OK, benchmark will catch real errors

    start = time.monotonic()
    try:
        response = await llm_client.complete(
            model=effective_model,
            messages=[
                {"role": "system", "content": "You are a peer reviewer. Return only valid JSON."},
                {"role": "user", "content": _BENCHMARK_PROMPT},
            ],
            session_id=session_id,
            **extra_kwargs,
        )
        elapsed = time.monotonic() - start
        result["latency_seconds"] = round(elapsed, 2)

        # Use the provider-reported token count, not our guess
        completion_tokens = response.usage.completion_tokens
        if completion_tokens and elapsed > 0:
            result["tokens_per_second"] = round(completion_tokens / elapsed, 1)
        result["completion_tokens"] = completion_tokens
        result["prompt_tokens"] = response.usage.prompt_tokens

        content = response.content.strip()
        score = 0

        # Parse JSON
        parsed = None
        try:
            parsed = json.loads(content)
            result["output_valid_json"] = True
            score += 30
        except json.JSONDecodeError:
            match = re.search(r"```(?:json)?\s*\n(.*?)```", content, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(1))
                    result["output_valid_json"] = True
                    score += 25
                except json.JSONDecodeError:
                    pass

        if parsed and isinstance(parsed, dict):
            required = {"summary", "strengths", "weaknesses", "overall_merit", "confidence"}
            present = required.intersection(parsed.keys())
            score += int(len(present) / len(required) * 30)
            result["output_complete"] = len(present) >= 4

            strengths = parsed.get("strengths", [])
            weaknesses = parsed.get("weaknesses", [])
            if isinstance(strengths, list) and len(strengths) >= 2:
                score += 10
            if isinstance(weaknesses, list) and len(weaknesses) >= 2:
                score += 10

            merit = parsed.get("overall_merit", {})
            if isinstance(merit, dict) and merit.get("score") in (1, 2, 3, 4, 5):
                score += 10
                result["output_has_scores"] = True
            conf = parsed.get("confidence", {})
            if isinstance(conf, dict) and conf.get("score") in (1, 2, 3, 4, 5):
                score += 10

        result["score"] = score
        result["status"] = "complete"
        result["raw_output"] = content[:500]

        # Classify for ProtoNeo review quality
        if score >= 90:
            result["protoneo_class"] = "excellent"
        elif score >= 70:
            result["protoneo_class"] = "good"
        elif score >= 50:
            result["protoneo_class"] = "usable"
        elif score >= 30:
            result["protoneo_class"] = "limited"
        else:
            result["protoneo_class"] = "unsuitable"

    except Exception as e:
        elapsed = time.monotonic() - start
        result["latency_seconds"] = round(elapsed, 2)
        result["status"] = "error"
        result["error"] = str(e)
        result["protoneo_class"] = "error"

    return result
