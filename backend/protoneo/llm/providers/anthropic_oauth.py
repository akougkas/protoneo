"""Anthropic Claude Max OAuth provider.

Implements the exact same PKCE OAuth flow that pi-ai uses to authenticate
with Claude Max subscriptions. The access token is an `sk-ant-oat...` token
that works as a standard Anthropic API key.

Flow:
1. Build auth URL with PKCE challenge
2. User opens URL in browser, logs into claude.ai
3. User pastes back the code (format: code#state)
4. Exchange code for access_token + refresh_token
5. Token auto-refreshes when expired
"""

import logging
import time

import httpx

from .oauth_base import OAuthCredentials, OAuthProvider

logger = logging.getLogger("protoneo.llm.providers.anthropic")

# Endpoints and credentials matching pi-ai's implementation
_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
_TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
_REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_SCOPES = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"

# 5-minute buffer before expiry (matches pi-ai)
_EXPIRY_BUFFER_SECONDS = 5 * 60


class AnthropicOAuth(OAuthProvider):
    """Claude Max subscription OAuth provider."""

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def display_name(self) -> str:
        return "Claude Max"

    @property
    def authorize_url(self) -> str:
        return _AUTHORIZE_URL

    @property
    def token_url(self) -> str:
        return _TOKEN_URL

    @property
    def client_id(self) -> str:
        return _CLIENT_ID

    @property
    def redirect_uri(self) -> str:
        return _REDIRECT_URI

    @property
    def scopes(self) -> str:
        return _SCOPES

    def build_auth_url(self) -> dict[str, str]:
        """Build auth URL with state=verifier (Anthropic-specific, matches pi-ai)."""
        from .oauth_base import generate_pkce
        from urllib.parse import urlencode

        verifier, challenge = generate_pkce()
        # Pi-ai uses the PKCE verifier as the state parameter for Anthropic
        state = verifier

        params = self._build_auth_params(challenge, state)
        url = f"{self.authorize_url}?{urlencode(params)}"

        return {"url": url, "verifier": verifier, "state": state,
                "needs_local_server": self.needs_local_server}

    def _build_auth_params(self, challenge: str, state: str) -> dict[str, str]:
        return {
            "code": "true",
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }

    async def _exchange_code_impl(self, code: str, verifier: str, state: str) -> OAuthCredentials:
        """Exchange authorization code for Anthropic tokens."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.token_url,
                json={
                    "grant_type": "authorization_code",
                    "client_id": self.client_id,
                    "code": code,
                    "state": state,
                    "redirect_uri": self.redirect_uri,
                    "code_verifier": verifier,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        expires_in = data.get("expires_in", 3600)
        return OAuthCredentials(
            access=data["access_token"],
            refresh=data["refresh_token"],
            expires=time.time() + expires_in - _EXPIRY_BUFFER_SECONDS,
            extra={"token_type": "oauth"},
        )

    async def _refresh_async(self, creds: OAuthCredentials) -> OAuthCredentials:
        """Refresh Anthropic OAuth token."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.token_url,
                json={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "refresh_token": creds.refresh,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        expires_in = data.get("expires_in", 3600)
        return OAuthCredentials(
            access=data["access_token"],
            refresh=data["refresh_token"],
            expires=time.time() + expires_in - _EXPIRY_BUFFER_SECONDS,
            extra={"token_type": creds.extra.get("token_type", "oauth")},
        )
