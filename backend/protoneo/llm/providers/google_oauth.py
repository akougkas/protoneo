"""Google Cloud Code Assist OAuth providers.

Two providers for Google's Cloud Code Assist API (subscription-based):

1. GoogleGeminiOAuth ("google"): Uses Gemini CLI OAuth credentials.
   Endpoint: cloudcode-pa.googleapis.com
   Can import tokens from ~/.gemini/oauth_creds.json

2. GoogleAntigravityOAuth ("google-antigravity"): Uses Antigravity OAuth credentials.
   Endpoints: daily sandbox -> autopush sandbox -> production (fallback chain)
   Includes additional scopes for experiments and logging.

Both providers use PKCE OAuth with a local callback server and communicate
with the Cloud Code Assist generateContent API. They do NOT use
generativelanguage.googleapis.com (Gemini API) or Vertex AI.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

from .oauth_base import OAuthCredentials, OAuthProvider, load_credentials, save_credentials

logger = logging.getLogger("protoneo.llm.providers.google")

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"

# 1-minute buffer before expiry. Google access tokens are always 1 hour
# (set by Google's OAuth server, cannot be extended). The refresh_token is
# permanent and auto-renews the access token transparently on every API call.
_EXPIRY_BUFFER_SECONDS = 60


# ── Gemini CLI constants ──────────────────────────────────────

_GEMINI_CLIENT_ID = (
    "681255809395-oo8ft2oprdrn"
    "p9e3aqf6av3hmdib135j"
    ".apps.googleusercontent.com"
)
_GEMINI_CLIENT_SECRET = "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"
_GEMINI_REDIRECT_URI = "http://localhost:8085/oauth2callback"
_GEMINI_SCOPES = " ".join([
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
])
GEMINI_ENDPOINT = "https://cloudcode-pa.googleapis.com"
GEMINI_USER_AGENT = "google-cloud-sdk vscode_cloudshelleditor/0.1"


# ── Antigravity constants ─────────────────────────────────────

_AG_CLIENT_ID = (
    "1071006060591-tmhssin2h21lcre235vtolojh4g403ep"
    ".apps.googleusercontent.com"
)
_AG_CLIENT_SECRET = "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"
_AG_REDIRECT_URI = "http://localhost:51121/oauth-callback"
_AG_SCOPES = " ".join([
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
])
AG_ENDPOINTS = [
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
    "https://autopush-cloudcode-pa.sandbox.googleapis.com",
    "https://cloudcode-pa.googleapis.com",
]
AG_USER_AGENT = "antigravity/1.18.4 darwin/arm64"


# ── Shared helpers ─────────────────────────────────────────────

async def _discover_project(access_token: str, endpoint: str) -> str:
    """Discover the user's Google Cloud project for Code Assist.

    Calls POST /v1internal:loadCodeAssist on the given endpoint.
    Does NOT attempt free-tier onboarding. The user must already
    have a subscription provisioned through the Gemini CLI or
    Antigravity.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.post(
                f"{endpoint}/v1internal:loadCodeAssist",
                headers=headers,
                json={
                    "metadata": {
                        "ideType": "IDE_UNSPECIFIED",
                        "platform": "PLATFORM_UNSPECIFIED",
                        "pluginType": "GEMINI",
                    }
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                project = data.get("cloudaicompanionProject", "")
                if isinstance(project, dict):
                    project = project.get("id", "")
                if project:
                    return project
            else:
                logger.warning(
                    "loadCodeAssist returned %d on %s: %s",
                    resp.status_code, endpoint, resp.text[:200],
                )
        except Exception as e:
            logger.warning("Project discovery failed on %s: %s", endpoint, e)

    return ""


async def _get_user_email(access_token: str) -> str:
    """Get the authenticated user's email address."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                "https://www.googleapis.com/oauth2/v1/userinfo",
                params={"alt": "json"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code == 200:
                return resp.json().get("email", "")
        except Exception:
            pass
    return ""


async def _exchange_google_code(
    token_url: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    code: str,
    verifier: str,
) -> dict:
    """Exchange authorization code for Google tokens."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            token_url,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json()


async def _refresh_google_token(
    token_url: str,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict:
    """Refresh a Google OAuth token."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            token_url,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        return resp.json()


# ── Provider 1: Gemini CLI ─────────────────────────────────────

class GoogleGeminiOAuth(OAuthProvider):
    """Google Gemini CLI subscription OAuth provider.

    Authenticates via the same OAuth client as the official Gemini CLI,
    giving ProtoNeo access to the user's Gemini Pro/Ultra subscription
    through Cloud Code Assist.

    Can import existing credentials from ~/.gemini/oauth_creds.json.
    """

    _GEMINI_CLI_CREDS = Path.home() / ".gemini" / "oauth_creds.json"

    @property
    def provider_name(self) -> str:
        return "google"

    @property
    def display_name(self) -> str:
        return "Gemini Pro/Ultra"

    @property
    def authorize_url(self) -> str:
        return _AUTH_URL

    @property
    def token_url(self) -> str:
        return _TOKEN_URL

    @property
    def client_id(self) -> str:
        return _GEMINI_CLIENT_ID

    @property
    def redirect_uri(self) -> str:
        return _GEMINI_REDIRECT_URI

    @property
    def scopes(self) -> str:
        return _GEMINI_SCOPES

    @property
    def connection_hint(self) -> str:
        return "Log in with your Google account (Gemini Pro/Ultra subscription)."

    def _build_auth_params(self, challenge: str, state: str) -> dict[str, str]:
        return {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }

    async def _exchange_code_impl(
        self, code: str, verifier: str, state: str,
    ) -> OAuthCredentials:
        data = await _exchange_google_code(
            self.token_url, self.client_id, _GEMINI_CLIENT_SECRET,
            self.redirect_uri, code, verifier,
        )

        access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)

        project_id, email = await asyncio.gather(
            _discover_project(access_token, GEMINI_ENDPOINT),
            _get_user_email(access_token),
        )

        if not project_id:
            logger.warning(
                "No Google Cloud project discovered. "
                "Run the Gemini CLI first to provision your subscription."
            )

        return OAuthCredentials(
            access=access_token,
            refresh=data.get("refresh_token", ""),
            expires=time.time() + expires_in - _EXPIRY_BUFFER_SECONDS,
            extra={"projectId": project_id, "email": email, "token_type": "oauth"},
        )

    async def _refresh_async(self, creds: OAuthCredentials) -> OAuthCredentials:
        data = await _refresh_google_token(
            self.token_url, self.client_id, _GEMINI_CLIENT_SECRET, creds.refresh,
        )

        expires_in = data.get("expires_in", 3600)
        new_access = data["access_token"]

        project_id = creds.extra.get("projectId", "")
        if not project_id:
            project_id = await _discover_project(new_access, GEMINI_ENDPOINT)

        return OAuthCredentials(
            access=new_access,
            refresh=creds.refresh,
            expires=time.time() + expires_in - _EXPIRY_BUFFER_SECONDS,
            extra={
                "projectId": project_id,
                "email": creds.extra.get("email", ""),
                "token_type": "oauth",
            },
        )

    async def get_credentials_async(self) -> OAuthCredentials | None:
        """Get valid credentials, importing from Gemini CLI if needed."""
        creds = load_credentials(self.provider_name)

        if creds and creds.refresh:
            if not creds.expired:
                return creds
            try:
                refreshed = await self._refresh_async(creds)
                save_credentials(self.provider_name, refreshed)
                return refreshed
            except Exception as e:
                logger.warning("Google token refresh failed: %s", e)

        imported = await self._import_gemini_cli_credentials()
        if imported:
            return imported

        return creds

    async def _import_gemini_cli_credentials(self) -> OAuthCredentials | None:
        """Import credentials from ~/.gemini/oauth_creds.json.

        The Gemini CLI uses the same OAuth client ID, so the
        refresh_token works with our client as well.
        """
        if not self._GEMINI_CLI_CREDS.exists():
            return None

        try:
            data = json.loads(self._GEMINI_CLI_CREDS.read_text())
            refresh_token = data.get("refresh_token", "")
            if not refresh_token:
                return None

            logger.info(
                "Importing credentials from Gemini CLI (%s)",
                self._GEMINI_CLI_CREDS,
            )

            token_data = await _refresh_google_token(
                self.token_url, self.client_id, _GEMINI_CLIENT_SECRET, refresh_token,
            )

            access_token = token_data["access_token"]
            expires_in = token_data.get("expires_in", 3600)

            project_id, email = await asyncio.gather(
                _discover_project(access_token, GEMINI_ENDPOINT),
                _get_user_email(access_token),
            )

            creds = OAuthCredentials(
                access=access_token,
                refresh=refresh_token,
                expires=time.time() + expires_in - _EXPIRY_BUFFER_SECONDS,
                extra={"projectId": project_id, "email": email, "token_type": "oauth"},
            )
            save_credentials(self.provider_name, creds)
            logger.info(
                "Gemini CLI credentials imported: project=%s email=%s",
                project_id, email,
            )
            return creds

        except Exception as e:
            logger.warning("Failed to import Gemini CLI credentials: %s", e)
            return None


# ── Provider 2: Antigravity ────────────────────────────────────

class GoogleAntigravityOAuth(OAuthProvider):
    """Google Antigravity subscription OAuth provider.

    Antigravity accesses a wider set of models (including Claude and
    GPT served through Google) and uses a fallback endpoint chain
    through sandbox environments before hitting production.
    """

    @property
    def provider_name(self) -> str:
        return "google-antigravity"

    @property
    def display_name(self) -> str:
        return "Antigravity"

    @property
    def authorize_url(self) -> str:
        return _AUTH_URL

    @property
    def token_url(self) -> str:
        return _TOKEN_URL

    @property
    def client_id(self) -> str:
        return _AG_CLIENT_ID

    @property
    def redirect_uri(self) -> str:
        return _AG_REDIRECT_URI

    @property
    def scopes(self) -> str:
        return _AG_SCOPES

    @property
    def connection_hint(self) -> str:
        return "Log in with your Google account (Antigravity, includes Gemini 3 + Claude + GPT)."

    @property
    def oauth_experimental(self) -> bool:
        return True

    def _build_auth_params(self, challenge: str, state: str) -> dict[str, str]:
        return {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }

    async def _exchange_code_impl(
        self, code: str, verifier: str, state: str,
    ) -> OAuthCredentials:
        data = await _exchange_google_code(
            self.token_url, self.client_id, _AG_CLIENT_SECRET,
            self.redirect_uri, code, verifier,
        )

        access_token = data["access_token"]
        expires_in = data.get("expires_in", 3600)

        project_id, email = await asyncio.gather(
            self._discover_project_with_fallback(access_token),
            _get_user_email(access_token),
        )

        if not project_id:
            logger.warning("No Antigravity project discovered.")

        return OAuthCredentials(
            access=access_token,
            refresh=data.get("refresh_token", ""),
            expires=time.time() + expires_in - _EXPIRY_BUFFER_SECONDS,
            extra={"projectId": project_id, "email": email, "token_type": "oauth"},
        )

    async def _refresh_async(self, creds: OAuthCredentials) -> OAuthCredentials:
        data = await _refresh_google_token(
            self.token_url, self.client_id, _AG_CLIENT_SECRET, creds.refresh,
        )

        expires_in = data.get("expires_in", 3600)
        new_access = data["access_token"]

        project_id = creds.extra.get("projectId", "")
        if not project_id:
            project_id = await self._discover_project_with_fallback(new_access)

        return OAuthCredentials(
            access=new_access,
            refresh=creds.refresh,
            expires=time.time() + expires_in - _EXPIRY_BUFFER_SECONDS,
            extra={
                "projectId": project_id,
                "email": creds.extra.get("email", ""),
                "token_type": "oauth",
            },
        )

    @staticmethod
    async def _discover_project_with_fallback(access_token: str) -> str:
        """Try each Antigravity endpoint until project discovery succeeds."""
        for endpoint in AG_ENDPOINTS:
            project_id = await _discover_project(access_token, endpoint)
            if project_id:
                return project_id
        return ""
