"""OAuth provider registry.

Manages all subscription-based providers and provides a unified interface
for login, logout, token resolution, and status checks.

Credential resolution priority (matching pi-ai):
1. OAuth token from stored credentials (subscription login)
2. Environment variable API key (standard API billing)
3. None (provider not available)
"""

import logging
import os
from typing import Any

from .oauth_base import OAuthProvider, load_credentials
# from .anthropic_oauth import AnthropicOAuth  # DISABLED: Anthropic provider removed
from .openai_oauth import OpenAIOAuth

logger = logging.getLogger("protoneo.llm.providers.registry")


class ProviderRegistry:
    """Central registry for OAuth-based subscription providers.

    Usage::

        registry = ProviderRegistry()

        # Check which providers are available
        status = registry.all_status()

        # Start login flow for Claude Max
        auth_info = registry.begin_login("anthropic")
        # User completes browser flow, returns code
        creds = await registry.complete_login("anthropic", code, verifier, state)

        # Get API key for a provider (OAuth token or env var)
        key = registry.resolve_api_key("anthropic")
    """

    def __init__(self):
        self._providers: dict[str, OAuthProvider] = {
            # "anthropic": AnthropicOAuth(),  # DISABLED: Anthropic provider removed
            "openai": OpenAIOAuth(),
        }

    def get_provider(self, name: str) -> OAuthProvider | None:
        return self._providers.get(name)

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())

    def begin_login(self, provider_name: str) -> dict[str, str]:
        """Start the OAuth login flow for a provider.

        Returns {"url": auth_url, "verifier": pkce_verifier, "state": state}.
        The frontend should open the URL in a browser window.
        """
        provider = self._providers.get(provider_name)
        if not provider:
            raise ValueError(f"Unknown provider: {provider_name}")
        if not provider.oauth_enabled:
            raise ValueError(
                provider.connection_hint
                or f"{provider.display_name} OAuth is currently unavailable."
            )
        return provider.build_auth_url()

    async def complete_login(
        self, provider_name: str, code: str, verifier: str, state: str
    ) -> dict[str, Any]:
        """Complete the OAuth login flow after user authorizes.

        The code can be in any format the provider supports:
        - Full redirect URL
        - code#state format
        - Raw code
        """
        provider = self._providers.get(provider_name)
        if not provider:
            raise ValueError(f"Unknown provider: {provider_name}")

        creds = await provider.exchange_code(code, verifier, state)
        return {
            "provider": provider_name,
            "logged_in": True,
            "expires_at": creds.expires,
            **{
                k: v
                for k, v in creds.extra.items()
                if k in ("email", "accountId", "projectId", "token_type")
            },
        }

    def logout(self, provider_name: str) -> None:
        provider = self._providers.get(provider_name)
        if provider:
            provider.logout()

    def resolve_api_key(self, provider_name: str) -> str | None:
        """Resolve the API key for a provider. Safe to call from sync or async.

        Priority:
        1. OAuth stored token (no refresh, just reads file)
        2. Environment variable (standard API key)

        Does NOT refresh expired tokens. If a stored token is expired,
        the API call will fail with 401 and the user should re-login
        or use resolve_api_key_async() which handles refresh.
        """
        return self.resolve_credential_info(provider_name).get("api_key")

    async def resolve_api_key_async(self, provider_name: str) -> str | None:
        """Async version that refreshes expired tokens before returning."""
        provider = self._providers.get(provider_name)
        if provider:
            creds = await provider.get_credentials_async()
            if creds:
                return creds.access

        return self._env_key(provider_name)

    def resolve_credential_info(self, provider_name: str) -> dict[str, Any]:
        """Return the credential source and token type for a provider."""
        provider = self._providers.get(provider_name)
        if provider:
            creds = load_credentials(provider_name)
            if creds:
                return {
                    "provider": provider_name,
                    "api_key": creds.access,
                    "api_key_source": "oauth",
                    "token_type": creds.extra.get("token_type", "oauth"),
                    "logged_in": True,
                    "oauth_enabled": provider.oauth_enabled,
                    "oauth_experimental": provider.oauth_experimental,
                    "connection_hint": provider.connection_hint,
                }

        env_key = self._env_key(provider_name)
        if env_key:
            return {
                "provider": provider_name,
                "api_key": env_key,
                "api_key_source": "env",
                "token_type": "api_key",
                "logged_in": False,
                "oauth_enabled": provider.oauth_enabled if provider else False,
                "oauth_experimental": provider.oauth_experimental if provider else False,
                "connection_hint": provider.connection_hint if provider else None,
            }

        return {
            "provider": provider_name,
            "api_key": None,
            "api_key_source": "none",
            "token_type": "",
            "logged_in": False,
            "oauth_enabled": provider.oauth_enabled if provider else False,
            "oauth_experimental": provider.oauth_experimental if provider else False,
            "connection_hint": provider.connection_hint if provider else None,
        }

    @staticmethod
    def _env_key(provider_name: str) -> str | None:
        env_map = {
            # "anthropic": ["ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_API_KEY"],  # DISABLED
            "openai": ["OPENAI_API_KEY"],
        }
        for env_var in env_map.get(provider_name, []):
            val = os.getenv(env_var)
            if val:
                return val
        return None

    def all_status(self) -> list[dict[str, Any]]:
        """Get login status for all providers."""
        statuses = []
        for provider in self._providers.values():
            status = provider.status()
            credential_info = self.resolve_credential_info(provider.provider_name)
            status["has_credentials"] = credential_info["api_key"] is not None
            status["api_key_source"] = credential_info["api_key_source"]
            status["token_type"] = credential_info["token_type"]
            statuses.append(status)
        return statuses

    def provider_status(self, provider_name: str) -> dict[str, Any]:
        provider = self._providers.get(provider_name)
        if not provider:
            return {"provider": provider_name, "logged_in": False, "error": "Unknown provider"}
        status = provider.status()
        credential_info = self.resolve_credential_info(provider_name)
        status["has_credentials"] = credential_info["api_key"] is not None
        status["api_key_source"] = credential_info["api_key_source"]
        status["token_type"] = credential_info["token_type"]
        return status


# Singleton instance
_registry: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    """Get or create the global provider registry singleton."""
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
