"""
Multi-provider LLM client.

Routes through LiteLLM for local/homelab/OpenRouter endpoints, and makes
direct HTTP calls for subscription providers whose OAuth tokens require
provider-specific API endpoints and auth headers:

- OpenAI (ChatGPT Plus): Direct HTTP to chatgpt.com/backend-api (Codex Responses API)

Anthropic provider has been disabled.
"""

import asyncio
import base64
import json
import logging
import re
from collections import defaultdict
from typing import Any, AsyncGenerator, Callable

import httpx
import litellm
from litellm import acompletion, ModelResponse
from litellm.exceptions import (
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
    APIConnectionError,
    APIError,
)

from .registry import CapabilityRegistry
from .types import LLMResponse, ModelInfo, TokenUsage

logger = logging.getLogger("protoneo.llm.client")

litellm.suppress_debug_info = True
litellm.drop_params = True

_MAX_RETRIES = 3
_BASE_DELAY = 2.0
_RETRYABLE_EXCEPTIONS = (
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
    APIConnectionError,
)

# DISABLED: Anthropic provider removed from ProtoNeo
# _ANTHROPIC_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."
# _ANTHROPIC_BETA = "claude-code-20250219,oauth-2025-04-20,fine-grained-tool-streaming-2025-05-14,interleaved-thinking-2025-05-14"

# OpenAI Codex (ChatGPT subscription) endpoint
_CODEX_BASE_URL = "https://chatgpt.com/backend-api"
_CODEX_JWT_CLAIM = "https://api.openai.com/auth"


def _is_anthropic_oauth(provider: str, api_key: str) -> bool:
    # DISABLED: Anthropic provider removed. Always returns False.
    return False  # provider == "anthropic" and api_key.startswith("sk-ant-oat")


def _is_openai_oauth(provider: str, api_key: str) -> bool:
    """OpenAI OAuth tokens are JWTs (eyJ...) from ChatGPT subscription login."""
    return provider == "openai" and api_key.startswith("eyJ")



def _extract_openai_account_id(jwt_token: str) -> str:
    """Extract chatgpt_account_id from OpenAI OAuth JWT."""
    try:
        parts = jwt_token.split(".")
        if len(parts) < 2:
            return ""
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        auth_claim = payload.get(_CODEX_JWT_CLAIM, {})
        return auth_claim.get("chatgpt_account_id", "")
    except Exception:
        return ""


class LLMClient:
    """
    Multi-provider LLM client.

    Routes through LiteLLM for providers that use standard API key auth.
    Makes direct HTTP calls for subscription providers (OpenAI ChatGPT)
    that require non-standard endpoints and auth mechanisms.
    """

    def __init__(
        self,
        registry: CapabilityRegistry,
        api_keys: dict[str, str] | None = None,
        base_urls: dict[str, str] | None = None,
    ):
        self.registry = registry
        self._api_keys = api_keys or {}
        self._base_urls = base_urls or {}
        self._session_costs: dict[str, float] = defaultdict(float)

    async def _resolve_api_key_async(self, provider: str) -> str | None:
        """Resolve API key with async token refresh."""
        try:
            from .providers.registry import get_provider_registry
            key = await get_provider_registry().resolve_api_key_async(provider)
            if key:
                return key
        except Exception:
            pass

        if provider in self._api_keys:
            return self._api_keys[provider]
        if provider == "unknown" and "legacy" in self._api_keys:
            return self._api_keys["legacy"]
        return None

    # ── Direct provider calls (subscription OAuth) ───────────

    async def _call_openai_codex(
        self,
        token: str,
        messages: list[dict],
        model_id: str,
        temperature: float = 1,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Call OpenAI Codex Responses API (chatgpt.com/backend-api).

        Uses the Codex Responses SSE endpoint at chatgpt.com/backend-api/codex/responses.
        Requires curl_cffi for TLS impersonation (Cloudflare blocks standard Python clients).
        The API requires stream=True, store=False, and an instructions field.
        Does not support temperature, max_output_tokens, or other standard OpenAI params.
        """
        from curl_cffi.requests import AsyncSession

        account_id = _extract_openai_account_id(token)
        if not account_id:
            raise ValueError("Cannot extract accountId from OpenAI OAuth token")

        # Convert messages to Codex Responses API format
        input_items = []
        system_text = "You are a helpful assistant."
        for msg in messages:
            role = msg.get("role", "user")
            text = msg.get("content", "")
            if role == "system":
                system_text = text
            elif role == "assistant":
                input_items.append({"role": "assistant", "content": [{"type": "output_text", "text": text}]})
            else:
                input_items.append({"role": "user", "content": [{"type": "input_text", "text": text}]})

        body = {
            "model": model_id,
            "instructions": system_text,
            "input": input_items,
            "stream": True,
            "store": False,
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "chatgpt-account-id": account_id,
            "originator": "pi",
            "Content-Type": "application/json",
            "OpenAI-Beta": "responses=experimental",
            "Accept": "text/event-stream",
        }

        async with AsyncSession(impersonate="chrome", timeout=120) as client:
            resp = await client.post(
                f"{_CODEX_BASE_URL}/codex/responses",
                headers=headers,
                json=body,
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"OpenAI Codex API error {resp.status_code}: {resp.text[:500]}"
                )

        # Parse SSE response to extract content and usage
        content = ""
        usage_data = {}
        for line in resp.text.split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
                etype = event.get("type", "")
                if etype == "response.output_text.delta":
                    content += event.get("delta", "")
                elif etype == "response.completed":
                    usage_data = event.get("response", {}).get("usage", {})
            except json.JSONDecodeError:
                pass

        usage = TokenUsage(
            prompt_tokens=usage_data.get("input_tokens", 0),
            completion_tokens=usage_data.get("output_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )

        return LLMResponse(content=content, model=model_id, usage=usage, raw={})

    # ── LiteLLM kwargs builder (for non-subscription providers) ──

    async def _build_kwargs_async(self, model: str, messages: list[dict], **overrides: Any) -> dict[str, Any]:
        """Build LiteLLM kwargs for local, homelab, OpenRouter, and API-key providers."""
        info: ModelInfo = self.registry.get(model)

        kwargs: dict[str, Any] = {
            "model": info.effective_model,
            "messages": list(messages),
        }

        if info.api_base:
            kwargs["api_base"] = info.api_base

        provider = info.provider
        api_key = await self._resolve_api_key_async(provider)
        if api_key:
            if False:  # DISABLED: Anthropic OAuth routing removed
                # if _is_anthropic_oauth(provider, api_key):
                #     kwargs["api_key"] = ""
                #     kwargs["extra_headers"] = {
                #         "Authorization": f"Bearer {api_key}",
                #         "anthropic-beta": _ANTHROPIC_BETA,
                #         "user-agent": "claude-cli/2.1.75",
                #         "x-app": "cli",
                #         "x-api-key": "",
                #     }
                #     msgs = kwargs["messages"]
                #     if msgs and msgs[0].get("role") == "system":
                #         msgs[0] = {**msgs[0], "content": _ANTHROPIC_SYSTEM_PREFIX + "\n\n" + msgs[0]["content"]}
                #     else:
                #         msgs.insert(0, {"role": "system", "content": _ANTHROPIC_SYSTEM_PREFIX})
                pass
            else:
                kwargs["api_key"] = api_key
        elif info.api_base:
            kwargs["api_key"] = "none"

        if "api_base" not in kwargs:
            if provider in self._base_urls:
                kwargs["api_base"] = self._base_urls[provider]
            elif provider == "unknown" and "legacy" in self._base_urls:
                kwargs["api_base"] = self._base_urls["legacy"]

        kwargs.update(overrides)
        return kwargs

    # ── Shared helpers ────────────────────────────────────────

    @staticmethod
    def _strip_thinking(content: str) -> str:
        """Remove <think>...</think> blocks emitted by reasoning models."""
        stripped = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()
        if stripped:
            return stripped
        match = re.search(r"<think>([\s\S]*?)</think>", content)
        if match:
            return match.group(1).strip()
        return content.strip()

    @staticmethod
    def _extract_usage(response: ModelResponse, model_info: ModelInfo) -> TokenUsage:
        """Pull token counts and cost from the LiteLLM response."""
        usage = getattr(response, "usage", None)
        if not usage:
            return TokenUsage()

        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        reasoning_tokens = 0
        if hasattr(usage, "completion_tokens_details"):
            details = usage.completion_tokens_details
            if details and hasattr(details, "reasoning_tokens"):
                reasoning_tokens = getattr(details, "reasoning_tokens", 0) or 0

        cost = (
            prompt_tokens * model_info.cost_per_input_token
            + completion_tokens * model_info.cost_per_output_token
        )

        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost=cost,
        )

    # ── Public API ────────────────────────────────────────────

    async def complete(
        self,
        model: str,
        messages: list[dict],
        session_id: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        max_retries: int = _MAX_RETRIES,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a chat completion request with retry on transient errors."""
        info: ModelInfo = self.registry.get(model)
        provider = info.provider

        # GPT-5 models only support temperature=1
        if "gpt-5" in model.lower():
            temperature = 1

        api_key = await self._resolve_api_key_async(provider)

        # Models with a custom api_base (local/homelab endpoints) always go
        # through LiteLLM, even if the provider name matches a subscription
        # provider. This prevents "openai/local-model" from being routed
        # through the ChatGPT Codex endpoint.
        has_local_endpoint = "api_base" in kwargs or bool(info.api_base)

        # Subscription providers with OAuth tokens bypass LiteLLM entirely
        if api_key and not has_local_endpoint and _is_openai_oauth(provider, api_key):
            raw_model = model.split("/", 1)[1] if "/" in model else model
            response = await self._call_openai_codex(
                token=api_key,
                messages=messages,
                model_id=raw_model,
                temperature=1,  # gpt-5 models only support temperature=1
                max_tokens=max_tokens,
            )
            response.content = self._strip_thinking(response.content)
            if session_id:
                self._session_costs[session_id] += response.usage.cost
            return response

        # All other providers: LiteLLM
        call_overrides: dict[str, Any] = {"temperature": temperature, **kwargs}
        if max_tokens is not None:
            call_overrides["max_tokens"] = max_tokens
        call_kwargs = await self._build_kwargs_async(model, messages, **call_overrides)

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                response: ModelResponse = await acompletion(**call_kwargs)
                break
            except _RETRYABLE_EXCEPTIONS as exc:
                last_error = exc
                if attempt == max_retries:
                    raise
                delay = _BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "LLM call attempt %d/%d failed (model=%s): %s. Retrying in %.1fs",
                    attempt, max_retries, model, exc, delay,
                )
                await asyncio.sleep(delay)
            except APIError as exc:
                status = getattr(exc, "status_code", None)
                if status and status >= 500 and attempt < max_retries:
                    last_error = exc
                    delay = _BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(
                        "LLM call attempt %d/%d got %d (model=%s): %s. Retrying in %.1fs",
                        attempt, max_retries, status, model, exc, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise

        msg = response.choices[0].message
        content = msg.content or ""

        reasoning_content = getattr(msg, "reasoning_content", None)
        if not content and reasoning_content:
            content = reasoning_content

        raw_len = len(content)
        content = self._strip_thinking(content)

        # Fix 1: Warn when strip_thinking removes all content (model produced
        # only <think> tokens with no final answer)
        if not content.strip() and raw_len > 0:
            logger.warning(
                "Model %s produced %d chars of thinking tokens but no final answer",
                model, raw_len,
            )

        model_info = self.registry.get(model)
        usage = self._extract_usage(response, model_info)

        if session_id:
            self._session_costs[session_id] += usage.cost

        return LLMResponse(
            content=content,
            model=model,
            usage=usage,
            raw=response.model_dump() if hasattr(response, "model_dump") else {},
        )

    async def stream(
        self,
        model: str,
        messages: list[dict],
        session_id: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream a chat completion, yielding content chunks."""
        usage_callback: Callable[[dict[str, int]], None] | None = kwargs.pop(
            "usage_callback",
            None,
        )
        info: ModelInfo = self.registry.get(model)
        provider = info.provider

        # GPT-5 models only support temperature=1
        if "gpt-5" in model.lower():
            temperature = 1

        api_key = await self._resolve_api_key_async(provider)

        # Models with a custom api_base (local/homelab endpoints) always go
        # through LiteLLM, even if the provider name matches a subscription
        # provider. This prevents "openai/local-model" from being routed
        # through the ChatGPT Codex endpoint.
        has_local_endpoint = "api_base" in kwargs or bool(info.api_base)

        # Subscription providers with direct HTTP paths do not yet expose
        # native streaming here, so reuse complete() and yield one chunk.
        if api_key and not has_local_endpoint:
            if _is_openai_oauth(provider, api_key):
                response = await self.complete(
                    model=model,
                    messages=messages,
                    session_id=session_id,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                if usage_callback:
                    usage_callback({
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "total_tokens": response.usage.total_tokens,
                    })
                if response.content:
                    yield response.content
                return

        call_overrides: dict[str, Any] = {
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
            **kwargs,
        }
        if max_tokens is not None:
            call_overrides["max_tokens"] = max_tokens
        call_kwargs = await self._build_kwargs_async(model, messages, **call_overrides)

        response = await acompletion(**call_kwargs)

        async for chunk in response:
            # Capture usage from the final chunk (choices may be empty)
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage:
                usage = {
                    "prompt_tokens": getattr(chunk_usage, "prompt_tokens", 0) or 0,
                    "completion_tokens": getattr(chunk_usage, "completion_tokens", 0) or 0,
                    "total_tokens": getattr(chunk_usage, "total_tokens", 0) or 0,
                }
                if usage_callback:
                    usage_callback(usage)
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                yield delta.content

    def session_cost(self, session_id: str) -> float:
        return self._session_costs.get(session_id, 0.0)

    def reset_session_cost(self, session_id: str) -> None:
        self._session_costs.pop(session_id, None)

    @classmethod
    def from_config(cls, config: "ProtoNeoConfig") -> "LLMClient":
        """Build a client from a ProtoNeoConfig instance."""
        from ..config.schema import ProtoNeoConfig
        from .settings import load_settings

        registry = CapabilityRegistry.from_settings(load_settings())
        api_keys: dict[str, str] = {}
        base_urls: dict[str, str] = {}

        for provider_name, provider_cfg in config.providers.items():
            if provider_cfg.api_key:
                api_keys[provider_name] = provider_cfg.api_key
            if provider_cfg.base_url:
                base_urls[provider_name] = provider_cfg.base_url

        return cls(registry=registry, api_keys=api_keys, base_urls=base_urls)
