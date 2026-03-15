"""
Multi-provider LLM client.

Routes through LiteLLM for local/homelab/OpenRouter endpoints, and makes
direct HTTP calls for subscription providers whose OAuth tokens require
provider-specific API endpoints and auth headers:

- Anthropic (Claude Max): LiteLLM with api_key='' and Bearer via extra_headers
- OpenAI (ChatGPT Plus): Direct HTTP to chatgpt.com/backend-api (Codex Responses API)
- Google (Gemini CLI): Direct HTTP to cloudcode-pa.googleapis.com (Cloud Code Assist)

These direct paths replicate the exact same HTTP calls that pi-ai makes.
"""

import asyncio
import base64
import json
import logging
import re
from collections import defaultdict
from typing import Any, AsyncGenerator

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

_MAX_RETRIES = 3
_BASE_DELAY = 2.0
_RETRYABLE_EXCEPTIONS = (
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
    APIConnectionError,
)

# Anthropic OAuth identity (matching pi-ai exactly)
_ANTHROPIC_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."
_ANTHROPIC_BETA = "claude-code-20250219,oauth-2025-04-20,fine-grained-tool-streaming-2025-05-14,interleaved-thinking-2025-05-14"

# OpenAI Codex (ChatGPT subscription) endpoint
_CODEX_BASE_URL = "https://chatgpt.com/backend-api"
_CODEX_JWT_CLAIM = "https://api.openai.com/auth"

# Google Cloud Code Assist: per-provider endpoint and header configuration
_GOOGLE_PROVIDER_CONFIG = {
    "google": {
        "endpoints": ["https://cloudcode-pa.googleapis.com"],
        "user_agent": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "request_type": None,
    },
    "google-antigravity": {
        "endpoints": [
            "https://daily-cloudcode-pa.sandbox.googleapis.com",
            "https://autopush-cloudcode-pa.sandbox.googleapis.com",
            "https://cloudcode-pa.googleapis.com",
        ],
        "user_agent": "antigravity/1.18.4 darwin/arm64",
        "request_type": "agent",
    },
}


def _is_anthropic_oauth(provider: str, api_key: str) -> bool:
    return provider == "anthropic" and api_key.startswith("sk-ant-oat")


def _is_openai_oauth(provider: str, api_key: str) -> bool:
    """OpenAI OAuth tokens are JWTs (eyJ...) from ChatGPT subscription login."""
    return provider == "openai" and api_key.startswith("eyJ")


def _is_google_oauth(provider: str, api_key: str) -> bool:
    """Google OAuth tokens are ya29. access tokens from Cloud Code Assist login."""
    return provider in ("google", "google-antigravity") and api_key.startswith("ya29.")


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
    Makes direct HTTP calls for subscription providers (Anthropic OAuth,
    OpenAI ChatGPT, Google Gemini CLI) that require non-standard endpoints
    and auth mechanisms.
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

    def _resolve_google_project_id(self, provider_name: str = "google") -> str:
        """Load projectId from stored Google OAuth credentials."""
        from .providers.oauth_base import load_credentials
        creds = load_credentials(provider_name)
        if creds:
            return creds.extra.get("projectId", "")
        return ""

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

    async def _call_google_cloud_code(
        self,
        token: str,
        messages: list[dict],
        model_id: str,
        provider_name: str = "google",
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Call Google Cloud Code Assist API.

        Supports both Gemini CLI (single endpoint) and Antigravity
        (fallback endpoint chain). Matches pi-ai's request envelope,
        headers, and retry logic.
        """
        config = _GOOGLE_PROVIDER_CONFIG.get(provider_name, _GOOGLE_PROVIDER_CONFIG["google"])

        project_id = self._resolve_google_project_id(provider_name)
        if not project_id:
            project_id = await self._discover_google_project(token, provider_name)
            if not project_id:
                raise ValueError(
                    f"No Google Cloud project for {provider_name}. Re-login via Settings."
                )

        # Build contents and extract system instruction
        contents = []
        system_instruction = None
        for msg in messages:
            role = msg.get("role", "user")
            text = msg.get("content", "")
            if role == "system":
                system_instruction = {"role": "user", "parts": [{"text": text}]}
            else:
                gemini_role = "model" if role == "assistant" else "user"
                contents.append({"role": gemini_role, "parts": [{"text": text}]})

        request: dict[str, Any] = {"contents": contents}
        generation_config: dict[str, Any] = {}
        if temperature is not None:
            generation_config["temperature"] = temperature
        if max_tokens:
            generation_config["maxOutputTokens"] = max_tokens
        if generation_config:
            request["generationConfig"] = generation_config
        if system_instruction:
            request["systemInstruction"] = system_instruction

        user_agent = config["user_agent"]
        body: dict[str, Any] = {
            "project": project_id,
            "model": model_id,
            "request": request,
            "userAgent": user_agent,
        }
        if config["request_type"]:
            body["requestType"] = config["request_type"]

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": user_agent,
            "X-Goog-Api-Client": "gl-node/22.17.0",
            "Client-Metadata": json.dumps({
                "ideType": "IDE_UNSPECIFIED",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }),
        }

        endpoints = config["endpoints"]
        max_retries = 3
        last_error = None

        async with httpx.AsyncClient(timeout=120.0) as client:
            for endpoint in endpoints:
                url = f"{endpoint}/v1internal:generateContent"
                for attempt in range(1, max_retries + 1):
                    try:
                        resp = await client.post(url, headers=headers, json=body)
                    except httpx.HTTPError as e:
                        last_error = e
                        if attempt < max_retries:
                            await asyncio.sleep(_BASE_DELAY * (2 ** (attempt - 1)))
                            continue
                        break

                    if resp.status_code == 429 and attempt < max_retries:
                        delay = self._parse_google_retry_delay(resp.text)
                        logger.info(
                            "Google rate limit on %s (attempt %d/%d), waiting %ds",
                            endpoint, attempt, max_retries, delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    if resp.status_code in (403, 404) and len(endpoints) > 1:
                        logger.info(
                            "Google %d on %s, trying next endpoint",
                            resp.status_code, endpoint,
                        )
                        last_error = RuntimeError(
                            f"Google {resp.status_code}: {resp.text[:200]}"
                        )
                        break  # next endpoint

                    if resp.status_code >= 500 and attempt < max_retries:
                        delay = _BASE_DELAY * (2 ** (attempt - 1))
                        logger.warning(
                            "Google %d on %s (attempt %d/%d), retrying in %.1fs",
                            resp.status_code, endpoint, attempt, max_retries, delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    if resp.status_code != 200:
                        last_error = RuntimeError(
                            f"Google Cloud Code API error {resp.status_code}: "
                            f"{resp.text[:500]}"
                        )
                        break

                    # Success
                    data = resp.json()
                    return self._parse_google_response(data, model_id)
                else:
                    continue  # ran out of retries for this endpoint, try next

        raise last_error or RuntimeError("All Google Cloud Code endpoints failed")

    @staticmethod
    def _parse_google_response(data: dict, model_id: str) -> "LLMResponse":
        """Parse Cloud Code Assist generateContent response."""
        response_data = data.get("response", data)

        content = ""
        candidates = response_data.get("candidates", [])
        if candidates:
            for part in candidates[0].get("content", {}).get("parts", []):
                text = part.get("text", "")
                if text and not part.get("thought"):
                    content += text

        usage_meta = response_data.get("usageMetadata", {})
        usage = TokenUsage(
            prompt_tokens=usage_meta.get("promptTokenCount", 0),
            completion_tokens=usage_meta.get("candidatesTokenCount", 0),
            total_tokens=usage_meta.get("totalTokenCount", 0),
        )

        return LLMResponse(content=content, model=model_id, usage=usage, raw=data)

    @staticmethod
    def _parse_google_retry_delay(error_text: str) -> int:
        """Extract retry delay from Google 429 error (matches pi-ai's extractRetryDelay).

        Parses patterns like "reset after 36s", "reset after 1m15s",
        "Please retry in 5s", and retryDelay JSON field.
        """
        # "reset after Xs" / "reset after XmYs" / "reset after XhYmZs"
        m = re.search(
            r"reset after (?:(\d+)h)?(?:(\d+)m)?(\d+(?:\.\d+)?)s",
            error_text, re.IGNORECASE,
        )
        if m:
            hours = int(m.group(1) or 0)
            minutes = int(m.group(2) or 0)
            seconds = float(m.group(3))
            return int(hours * 3600 + minutes * 60 + seconds + 1)

        # "Please retry in Xs"
        m = re.search(r"retry in (\d+(?:\.\d+)?)s", error_text, re.IGNORECASE)
        if m:
            return int(float(m.group(1)) + 1)

        # JSON retryDelay field: "34.074824224s"
        m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', error_text)
        if m:
            return int(float(m.group(1)) + 1)

        return 30

    async def _discover_google_project(
        self, token: str, provider_name: str = "google",
    ) -> str:
        """Discover and persist Google Cloud project via loadCodeAssist.

        No free-tier onboarding. The user must already have a subscription.
        For Antigravity, tries each endpoint in the fallback chain.
        """
        from .providers.google_oauth import (
            GEMINI_ENDPOINT, AG_ENDPOINTS, _discover_project,
        )

        if provider_name == "google-antigravity":
            endpoints = AG_ENDPOINTS
        else:
            endpoints = [GEMINI_ENDPOINT]

        for endpoint in endpoints:
            project_id = await _discover_project(token, endpoint)
            if project_id:
                self._persist_google_project(project_id, provider_name)
                return project_id

        return ""

    def _persist_google_project(
        self, project_id: str, provider_name: str = "google",
    ) -> None:
        """Save discovered projectId into stored credentials."""
        from .providers.oauth_base import load_credentials, save_credentials
        creds = load_credentials(provider_name)
        if creds:
            creds.extra["projectId"] = project_id
            save_credentials(provider_name, creds)
            logger.info(
                "Google project discovered and saved for %s: %s",
                provider_name, project_id,
            )

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
            if _is_anthropic_oauth(provider, api_key):
                # Anthropic OAuth: set api_key empty so LiteLLM doesn't send
                # x-api-key header, then inject Bearer via extra_headers.
                kwargs["api_key"] = ""
                kwargs["extra_headers"] = {
                    "Authorization": f"Bearer {api_key}",
                    "anthropic-beta": _ANTHROPIC_BETA,
                    "user-agent": "claude-cli/2.1.75",
                    "x-app": "cli",
                    "x-api-key": "",
                }
                # Mandatory system prompt for OAuth tokens
                msgs = kwargs["messages"]
                if msgs and msgs[0].get("role") == "system":
                    msgs[0] = {**msgs[0], "content": _ANTHROPIC_SYSTEM_PREFIX + "\n\n" + msgs[0]["content"]}
                else:
                    msgs.insert(0, {"role": "system", "content": _ANTHROPIC_SYSTEM_PREFIX})
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

        if api_key and not has_local_endpoint and _is_google_oauth(provider, api_key):
            raw_model = model.split("/", 1)[1] if "/" in model else model
            response = await self._call_google_cloud_code(
                token=api_key,
                messages=messages,
                model_id=raw_model,
                provider_name=provider,
                temperature=temperature,
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

        content = self._strip_thinking(content)

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
            if _is_openai_oauth(provider, api_key) or _is_google_oauth(provider, api_key):
                response = await self.complete(
                    model=model,
                    messages=messages,
                    session_id=session_id,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                if response.content:
                    yield response.content
                return

        call_overrides: dict[str, Any] = {"temperature": temperature, "stream": True, **kwargs}
        if max_tokens is not None:
            call_overrides["max_tokens"] = max_tokens
        call_kwargs = await self._build_kwargs_async(model, messages, **call_overrides)

        response = await acompletion(**call_kwargs)

        async for chunk in response:
            delta = chunk.choices[0].delta
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
