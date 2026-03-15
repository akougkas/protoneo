"""OpenAI ChatGPT Plus/Pro OAuth provider.

Implements the PKCE OAuth flow that pi-ai uses to authenticate with
ChatGPT Plus and Pro subscriptions. The access token is a JWT that
contains the user's accountId.

Flow:
1. Build auth URL with PKCE challenge
2. User opens URL, logs into OpenAI
3. User pastes back the redirect URL or code#state
4. Exchange code for JWT access_token + refresh_token
5. Extract accountId from JWT claims
6. Token auto-refreshes when expired
"""

import base64
import json
import logging
import time
from urllib.parse import urlencode

import httpx

from .oauth_base import OAuthCredentials, OAuthProvider

logger = logging.getLogger("protoneo.llm.providers.openai")

_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
_TOKEN_URL = "https://auth.openai.com/oauth/token"
_REDIRECT_URI = "http://localhost:1455/auth/callback"
_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_SCOPES = "openid profile email offline_access"
_JWT_CLAIM_PATH = "https://api.openai.com/auth"


def _extract_account_id(access_token: str) -> str:
    """Extract accountId from JWT access token.

    The OpenAI JWT contains a claim at 'https://api.openai.com/auth'
    with a 'chatgpt_account_id' field.
    """
    try:
        parts = access_token.split(".")
        if len(parts) < 2:
            return ""
        # JWT payload is base64url encoded
        payload_b64 = parts[1]
        # Add padding
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        auth_claim = payload.get(_JWT_CLAIM_PATH, {})
        return auth_claim.get("chatgpt_account_id", "")
    except Exception as e:
        logger.warning("Failed to extract accountId from JWT: %s", e)
        return ""


class OpenAIOAuth(OAuthProvider):
    """ChatGPT Plus/Pro subscription OAuth provider."""

    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def display_name(self) -> str:
        return "ChatGPT Plus/Pro"

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

    def _build_auth_params(self, challenge: str, state: str) -> dict[str, str]:
        return {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "protoneo",
        }

    async def _exchange_code_impl(self, code: str, verifier: str, state: str) -> OAuthCredentials:
        """Exchange authorization code for OpenAI tokens.

        OpenAI uses application/x-www-form-urlencoded for token exchange.
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.token_url,
                data={
                    "grant_type": "authorization_code",
                    "client_id": self.client_id,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": self.redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()

        access_token = data["access_token"]
        account_id = _extract_account_id(access_token)
        if not account_id:
            logger.warning("No accountId found in JWT. Token may not work for API calls.")

        expires_in = data.get("expires_in", 3600)
        return OAuthCredentials(
            access=access_token,
            refresh=data["refresh_token"],
            expires=time.time() + expires_in,
            extra={"accountId": account_id, "token_type": "oauth"},
        )

    async def _refresh_async(self, creds: OAuthCredentials) -> OAuthCredentials:
        """Refresh OpenAI OAuth token."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                self.token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": creds.refresh,
                    "client_id": self.client_id,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()

        access_token = data["access_token"]
        account_id = _extract_account_id(access_token)

        expires_in = data.get("expires_in", 3600)
        return OAuthCredentials(
            access=access_token,
            refresh=data["refresh_token"],
            expires=time.time() + expires_in,
            extra={
                "accountId": account_id or creds.extra.get("accountId", ""),
                "token_type": creds.extra.get("token_type", "oauth"),
            },
        )
