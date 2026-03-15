"""Dynamic model discovery and benchmarking.

Discovers models from all connected providers at runtime.
Nothing is hardcoded. Local services (LM Studio, Ollama) are
auto-detected. Cloud provider models come from their APIs.
"""

import asyncio
import json
import logging
import time
from typing import Any

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


async def _get_json(url: str, headers: dict | None = None, timeout: float = 5.0) -> dict | list | None:
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


async def _ensure_google_project_id(provider_name: str, api_key: str) -> str:
    """Resolve and persist the Cloud Code project for an OAuth provider."""
    from .providers.google_oauth import AG_ENDPOINTS, GEMINI_ENDPOINT, _discover_project
    from .providers.oauth_base import load_credentials, save_credentials

    creds = load_credentials(provider_name)
    if creds:
        project_id = str(creds.extra.get("projectId") or "")
        if project_id:
            return project_id
    else:
        project_id = ""

    endpoints = AG_ENDPOINTS if provider_name == "google-antigravity" else [GEMINI_ENDPOINT]
    for endpoint in endpoints:
        project_id = await _discover_project(api_key, endpoint)
        if project_id:
            if creds:
                creds.extra["projectId"] = project_id
                save_credentials(provider_name, creds)
            return project_id

    return ""


def _google_quota_endpoint_config(provider_name: str) -> tuple[list[str], str]:
    from .providers.google_oauth import AG_ENDPOINTS, AG_USER_AGENT, GEMINI_ENDPOINT, GEMINI_USER_AGENT

    if provider_name == "google-antigravity":
        return list(AG_ENDPOINTS), AG_USER_AGENT
    return [GEMINI_ENDPOINT], GEMINI_USER_AGENT


async def _discover_google_quota_models(provider_name: str, api_key: str) -> tuple[list[str], bool]:
    """Discover subscription models from Cloud Code quota buckets."""
    project_id = await _ensure_google_project_id(provider_name, api_key)
    if not project_id:
        return [], False

    endpoints, user_agent = _google_quota_endpoint_config(provider_name)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
        "X-Goog-Api-Client": "gl-node/22.17.0",
    }

    model_ids: list[str] = []
    online = False

    for endpoint in endpoints:
        data = await _post_json(
            f"{endpoint}/v1internal:retrieveUserQuota",
            body={"project": project_id},
            headers=headers,
            timeout=30.0,
        )
        if not data or not isinstance(data, dict):
            continue

        online = True
        for bucket in data.get("buckets", []):
            if not isinstance(bucket, dict):
                continue
            model_id = str(bucket.get("modelId") or "").strip()
            if model_id and model_id not in model_ids:
                model_ids.append(model_id)

        if provider_name == "google" and model_ids:
            break

    return model_ids, online


async def _validate_google_subscription_model(
    provider_name: str,
    api_key: str,
    project_id: str,
    model_id: str,
) -> bool | None:
    """Check whether a quota-listed model is callable through generateContent.

    Returns:
    - True: model is callable or temporarily capacity-constrained
    - False: model is listed in quota but rejects standard text generation
    - None: validation was inconclusive (timeout/network/rate-limit)
    """
    endpoints, user_agent = _google_quota_endpoint_config(provider_name)
    request: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": "Reply with exactly: ok"}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 8},
    }
    body: dict[str, Any] = {
        "project": project_id,
        "model": model_id,
        "request": request,
        "userAgent": user_agent,
    }
    if provider_name == "google-antigravity":
        body["requestType"] = "agent"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": user_agent,
        "X-Goog-Api-Client": "gl-node/22.17.0",
        "Client-Metadata": json.dumps({
            "ideType": "IDE_UNSPECIFIED",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
        }),
    }

    saw_inconclusive = False
    async with httpx.AsyncClient(timeout=20.0) as client:
        for endpoint in endpoints:
            try:
                resp = await client.post(
                    f"{endpoint}/v1internal:generateContent",
                    headers=headers,
                    json=body,
                )
            except httpx.TimeoutException:
                saw_inconclusive = True
                continue
            except httpx.HTTPError:
                saw_inconclusive = True
                continue

            if resp.status_code == 200:
                return True

            if resp.status_code == 503 and "MODEL_CAPACITY_EXHAUSTED" in resp.text:
                return True

            if resp.status_code in (429, 502, 503, 504):
                saw_inconclusive = True
                continue

            if resp.status_code in (400, 404, 500):
                continue

            saw_inconclusive = True

    return None if saw_inconclusive else False


async def _filter_google_subscription_models(provider_name: str, api_key: str, model_ids: list[str]) -> list[str]:
    """Drop quota-listed models that fail the provider's text generation path."""
    if not model_ids:
        return []

    project_id = await _ensure_google_project_id(provider_name, api_key)
    if not project_id:
        return model_ids

    validated: list[str] = []
    for model_id in model_ids:
        status = await _validate_google_subscription_model(
            provider_name,
            api_key,
            project_id,
            model_id,
        )
        if status is False:
            logger.info(
                "Filtered non-callable %s subscription model from quota discovery: %s",
                provider_name,
                model_id,
            )
            continue
        validated.append(model_id)

    return validated


def _subscription_models_from_ids(provider_name: str, model_ids: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "id": model_id,
            "name": model_id,
            "source": provider_name,
            "provider_type": "subscription",
        }
        for model_id in model_ids
    ]


# ── Local service detection ────────────────────────────────

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

        # llama-server exposes status and launch args per model
        status_info = m.get("status", {})
        if isinstance(status_info, dict):
            entry["loaded"] = status_info.get("value") == "loaded"
            if entry["loaded"]:
                loaded_model = mid

            # Parse server args for real config (ctx-size, temperature, etc.)
            args = status_info.get("args", [])
            if isinstance(args, list):
                for i, arg in enumerate(args):
                    if arg == "--ctx-size" and i + 1 < len(args):
                        try:
                            entry["context_length"] = int(args[i + 1])
                        except (ValueError, TypeError):
                            pass
                    elif arg == "--temperature" and i + 1 < len(args):
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
) -> dict[str, Any]:
    """Discover OpenAI models.

    OAuth tokens (ChatGPT subscription) get the static Codex catalog
    matching Codex CLI's /model list. API keys query /v1/models live.
    """
    is_oauth = _is_oauth_token("openai", api_key, credential_info)

    if is_oauth:
        from .catalogs import OPENAI_SUBSCRIPTION_MODELS
        return {
            "provider": "openai",
            "online": True,
            "models": list(OPENAI_SUBSCRIPTION_MODELS),
            "credential_type": "oauth",
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


async def discover_google(
    api_key: str,
    credential_info: dict[str, Any] | None = None,
    models_dev_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Discover Google Gemini models.

    OAuth tokens (Gemini CLI subscription) query the Cloud Code quota
    endpoint live. API keys query the public API.
    """
    is_oauth = _is_oauth_token("google", api_key, credential_info)

    if is_oauth:
        model_ids, online = await _discover_google_quota_models("google", api_key)
        model_ids = await _filter_google_subscription_models("google", api_key, model_ids)
        models = _subscription_models_from_ids("google", model_ids)
        return {
            "provider": "google",
            "online": online,
            "models": models,
            "credential_type": "oauth",
            **({"nudge": "Connected but no subscription models were returned."} if online and not models else {}),
        }

    # Standard API key: query live
    data = await _get_json(
        f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
        timeout=10.0,
    )

    models = []
    if data and isinstance(data, dict):
        for m in data.get("models", []):
            mid = m.get("name", "").replace("models/", "")
            if "gemini" in mid:
                models.append({
                    "id": mid,
                    "name": m.get("displayName", mid),
                    "context_length": m.get("inputTokenLimit", 0) + m.get("outputTokenLimit", 0),
                    "source": "google",
                    "provider_type": "api",
                })

    return {
        "provider": "google",
        "online": bool(models),
        "models": models,
        "credential_type": "env",
        **({"nudge": "No Gemini models found."} if not models else {}),
    }


async def discover_google_antigravity(
    api_key: str,
    credential_info: dict[str, Any] | None = None,
    models_dev_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Discover Google Antigravity models.

    Antigravity is OAuth-only, so discovery comes from live quota buckets.
    """
    is_oauth = _is_oauth_token("google-antigravity", api_key, credential_info)

    if is_oauth:
        model_ids, online = await _discover_google_quota_models("google-antigravity", api_key)
        model_ids = await _filter_google_subscription_models("google-antigravity", api_key, model_ids)
        models = _subscription_models_from_ids("google-antigravity", model_ids)
        return {
            "provider": "google-antigravity",
            "online": online,
            "models": models,
            "credential_type": "oauth",
            **({"nudge": "Connected but no Antigravity models were returned."} if online and not models else {}),
        }

    # Antigravity has no API-key path; if somehow called without OAuth,
    # return empty.
    return {
        "provider": "google-antigravity",
        "online": False,
        "models": [],
        "credential_type": "none",
        "nudge": "Antigravity requires OAuth login. Click Connect in Settings.",
    }


async def discover_all(
    localhost_endpoints: list[dict],
    lan_endpoints: list[dict],
    provider_credentials: dict[str, dict[str, Any]],
    openrouter_free_only: bool = True,
    cached_models: dict[str, list[dict[str, Any]]] | None = None,
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
    anthropic = provider_credentials.get("anthropic", {})
    if anthropic.get("api_key"):
        tasks["anthropic"] = discover_anthropic(anthropic["api_key"], anthropic)
    openai_creds = provider_credentials.get("openai", {})
    if openai_creds.get("api_key"):
        tasks["openai"] = discover_openai(openai_creds["api_key"], openai_creds)
    google = provider_credentials.get("google", {})
    if google.get("api_key"):
        tasks["google"] = discover_google(google["api_key"], google)

    google_ag = provider_credentials.get("google-antigravity", {})
    if google_ag.get("api_key"):
        tasks["google-antigravity"] = discover_google_antigravity(google_ag["api_key"], google_ag)

    results_list = await asyncio.gather(*tasks.values(), return_exceptions=True)

    results = {}
    for key, result in zip(tasks.keys(), results_list):
        if isinstance(result, Exception):
            results[key] = {"error": str(result)}
        else:
            results[key] = result

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
