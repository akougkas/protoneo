"""
Model capability registry built from ProtoNeo settings and discovery cache.

The source of truth is ~/.protoneo/settings.json:
- configured endpoint URLs
- discovered model listings
- persisted benchmark throughput/tags
- active model selections

This replaces the prior hardcoded homelab/static cloud registry.
"""

import logging
import re
from collections import defaultdict
from typing import Any

from .settings import ProtoNeoSettings, endpoint_alias_map, endpoint_map, load_settings
from .types import ModelCapability, ModelInfo, ModelTier

logger = logging.getLogger("protoneo.llm.registry")

_TAG_CAPABILITIES = {
    "structured": ModelCapability.STRUCTURED_OUTPUT,
    "reasoning": ModelCapability.EXTENDED_THINKING,
}

_VISION_HINTS = ("vision", "vl", "gpt-4.1", "claude", "ocr")
_FUNCTION_CALLING_PROVIDERS = {"anthropic", "openai", "openrouter"}


def _provider_prefixed_model_id(provider: str, model_id: str) -> str:
    return model_id if model_id.startswith(f"{provider}/") else f"{provider}/{model_id}"


def _raw_model_id(provider: str, model_id: str) -> str:
    prefix = f"{provider}/"
    if model_id.startswith(prefix):
        return model_id[len(prefix) :]
    return model_id


def _tokenize(value: str) -> set[str]:
    return set(re.findall(r"[a-z]+|\d+[a-z]*", value.lower()))


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


class CapabilityRegistry:
    """
    Maps model identifiers to routing/capability metadata.

    Registered models are keyed by a provider-prefixed ProtoNeo id
    (e.g. ``lan-mini/Qwen35-Distilled-i1-Q4_K_M``). ``get()`` also supports
    best-effort compatibility aliases such as older hand-written names.
    """

    def __init__(
        self,
        load_builtins: bool = True,
        settings: ProtoNeoSettings | None = None,
    ):
        self._models: dict[str, ModelInfo] = {}
        self._provider_models: dict[str, list[ModelInfo]] = defaultdict(list)
        self._raw_index: dict[str, list[ModelInfo]] = defaultdict(list)
        self._settings = settings or load_settings()
        self._provider_aliases = endpoint_alias_map(self._settings)

        if load_builtins:
            self._load_from_settings(self._settings)

    @classmethod
    def from_settings(cls, settings: ProtoNeoSettings | None = None) -> "CapabilityRegistry":
        """Build a registry from persisted ProtoNeo settings."""
        return cls(load_builtins=True, settings=settings)

    def _load_from_settings(self, settings: ProtoNeoSettings) -> None:
        endpoint_by_provider = endpoint_map(settings)
        benchmark_by_key = {
            (result.get("provider", ""), result.get("model_id", "")): result
            for result in settings.benchmark_results
            if isinstance(result, dict)
        }

        discovered = settings.discovered_models or {}
        for bucket, models in discovered.items():
            if not isinstance(models, list):
                continue
            for entry in models:
                if not isinstance(entry, dict):
                    continue
                provider = entry.get("source") or bucket
                raw_model_id = entry.get("id")
                if not provider or not raw_model_id:
                    continue

                info = self._build_model_info(
                    provider=provider,
                    raw_model_id=raw_model_id,
                    entry=entry,
                    benchmark=benchmark_by_key.get((provider, raw_model_id)),
                    endpoint=endpoint_by_provider.get(provider),
                )
                self.register(info)

        # Preserve active models even if discovery cache is stale/empty.
        for provider, raw_model_id in settings.active_models.items():
            if not raw_model_id:
                continue
            model_id = _provider_prefixed_model_id(provider, raw_model_id)
            if model_id in self._models:
                continue

            benchmark = benchmark_by_key.get((provider, raw_model_id))
            fallback_entry = {
                "id": raw_model_id,
                "name": raw_model_id,
                "source": provider,
                "provider_type": "local" if provider in endpoint_by_provider else "api",
            }
            self.register(
                self._build_model_info(
                    provider=provider,
                    raw_model_id=raw_model_id,
                    entry=fallback_entry,
                    benchmark=benchmark,
                    endpoint=endpoint_by_provider.get(provider),
                )
            )

    def _build_model_info(
        self,
        provider: str,
        raw_model_id: str,
        entry: dict[str, Any],
        benchmark: dict[str, Any] | None,
        endpoint,
    ) -> ModelInfo:
        model_id = _provider_prefixed_model_id(provider, raw_model_id)
        litellm_model = self._litellm_model(provider, raw_model_id, endpoint)
        capabilities = self._capabilities_for(provider, raw_model_id, entry, benchmark)
        tier = self._tier_for(provider, entry, endpoint)
        benchmark_throughput = ((benchmark or {}).get("throughput") or {}).get("tokens_per_second", 0)

        return ModelInfo(
            model_id=model_id,
            provider=provider,
            litellm_model=litellm_model,
            api_base=getattr(endpoint, "url", None),
            capabilities=capabilities,
            max_context=int(entry.get("context_length") or 128_000),
            speed_tps=int(round(benchmark_throughput or 0)),
            cost_per_input_token=float(entry.get("cost_prompt") or 0.0),
            cost_per_output_token=float(entry.get("cost_completion") or 0.0),
            tier=tier,
            display_name=str(entry.get("display_name") or entry.get("name") or raw_model_id),
        )

    @staticmethod
    def _litellm_model(provider: str, raw_model_id: str, endpoint) -> str | None:
        if endpoint is not None:
            prefix = "ollama_chat" if getattr(endpoint, "type", "openai") == "ollama" else "openai"
            return f"{prefix}/{raw_model_id}"
        if provider == "openrouter":
            return f"openrouter/{raw_model_id}"
        if provider == "anthropic":
            return f"anthropic/{raw_model_id}"
        if provider == "openai":
            return raw_model_id
        return None

    @staticmethod
    def _tier_for(provider: str, entry: dict[str, Any], endpoint) -> ModelTier:
        if endpoint is not None:
            return ModelTier.LOCAL
        if provider == "openrouter":
            return ModelTier.CLOUD_FREE if entry.get("is_free") else ModelTier.CLOUD_API
        provider_type = str(entry.get("provider_type") or "")
        if provider_type == "subscription":
            return ModelTier.SUBSCRIPTION
        return ModelTier.CLOUD_API

    @staticmethod
    def _capabilities_for(
        provider: str,
        raw_model_id: str,
        entry: dict[str, Any],
        benchmark: dict[str, Any] | None,
    ) -> set[ModelCapability]:
        capabilities: set[ModelCapability] = set()
        lower = raw_model_id.lower()

        if "embedding" not in lower and "embed" not in lower:
            capabilities.add(ModelCapability.STREAMING)
        if provider in _FUNCTION_CALLING_PROVIDERS:
            capabilities.add(ModelCapability.FUNCTION_CALLING)

        if any(hint in lower for hint in _VISION_HINTS):
            capabilities.add(ModelCapability.VISION)

        for tag in (benchmark or {}).get("tags", []):
            capability = _TAG_CAPABILITIES.get(tag)
            if capability is not None:
                capabilities.add(capability)

        if "reason" in lower or ("qwen" in lower and "i1" in lower):
            capabilities.add(ModelCapability.EXTENDED_THINKING)

        return capabilities

    def register(self, model: ModelInfo) -> None:
        self._models[model.model_id] = model
        self._provider_models[model.provider].append(model)
        self._raw_index[_raw_model_id(model.provider, model.model_id)].append(model)
        logger.debug("Registered model %s (provider=%s)", model.model_id, model.provider)

    def _resolve_provider_alias(self, provider: str, requested_raw_id: str) -> ModelInfo | None:
        candidates = self._provider_models.get(provider, [])
        if not candidates:
            return None

        # Exact raw id match first.
        for candidate in candidates:
            if _raw_model_id(provider, candidate.model_id) == requested_raw_id:
                return candidate

        requested_tokens = _tokenize(requested_raw_id)
        requested_norm = _normalize(requested_raw_id)

        best: tuple[float, ModelInfo] | None = None
        for candidate in candidates:
            raw_candidate = _raw_model_id(provider, candidate.model_id)
            candidate_tokens = _tokenize(raw_candidate)
            candidate_norm = _normalize(raw_candidate)

            score = 0.0
            if requested_norm and requested_norm in candidate_norm:
                score += 2.0
            if candidate_norm and candidate_norm in requested_norm:
                score += 1.0

            overlap = requested_tokens & candidate_tokens
            if requested_tokens:
                score += len(overlap) / len(requested_tokens)

            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, candidate)

        return best[1] if best and best[0] >= 1.0 else None

    def get(self, model_id: str) -> ModelInfo:
        """
        Return info for a model. Unknown models fall back gracefully so callers
        can still route arbitrary provider-prefixed strings.
        """
        if model_id in self._models:
            return self._models[model_id]

        # Unique raw ids can be resolved without a provider prefix.
        raw_candidates = self._raw_index.get(model_id, [])
        if len(raw_candidates) == 1:
            return raw_candidates[0]

        provider = model_id.split("/", 1)[0] if "/" in model_id else "unknown"
        requested_raw_id = model_id.split("/", 1)[1] if "/" in model_id else model_id
        # Map LiteLLM prefixes to canonical provider names
        _LITELLM_PROVIDER = {"ollama_chat": "ollama"}
        provider = _LITELLM_PROVIDER.get(provider, provider)
        mapped_provider = self._provider_aliases.get(provider, provider)
        exact_alias_id = _provider_prefixed_model_id(mapped_provider, requested_raw_id)
        if exact_alias_id in self._models:
            return self._models[exact_alias_id]

        provider_match = self._resolve_provider_alias(mapped_provider, requested_raw_id)
        if provider_match is not None:
            return provider_match

        fallback = ModelInfo(model_id=model_id, provider=mapped_provider)
        logger.debug("Model %s not in registry, using fallback (provider=%s)", model_id, mapped_provider)
        return fallback

    def find(self, required: set[ModelCapability]) -> list[ModelInfo]:
        """Return all registered models that have every capability in `required`."""
        return [m for m in self._models.values() if required <= m.capabilities]

    def find_by_tier(self, tier: ModelTier) -> list[ModelInfo]:
        """Return all models at a given billing tier."""
        return [m for m in self._models.values() if m.tier == tier]

    def find_available(self, api_keys: dict[str, str]) -> list[ModelInfo]:
        """Return models whose provider has credentials configured."""
        available = []
        for m in self._models.values():
            if m.tier == ModelTier.LOCAL:
                available.append(m)
            elif m.provider in api_keys:
                available.append(m)
        return available

    def list_all(self) -> list[ModelInfo]:
        return list(self._models.values())

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._models

    def __len__(self) -> int:
        return len(self._models)
