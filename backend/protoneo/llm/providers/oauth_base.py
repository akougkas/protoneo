"""Base OAuth infrastructure: PKCE, token storage, credential resolution.

Replicates pi-ai's OAuth patterns in Python. Each provider subclass implements
its own endpoints, client IDs, and token exchange logic.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

logger = logging.getLogger("protoneo.llm.oauth")

# Token storage location
_TOKEN_DIR = Path.home() / ".protoneo" / "tokens"


@dataclass
class OAuthCredentials:
    """Stored OAuth credentials with refresh capability."""
    access: str
    refresh: str
    expires: float  # Unix timestamp in seconds
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires

    def to_dict(self) -> dict[str, Any]:
        return {
            "access": self.access,
            "refresh": self.refresh,
            "expires": self.expires,
            **self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OAuthCredentials":
        extra = {k: v for k, v in data.items() if k not in ("access", "refresh", "expires")}
        return cls(
            access=data["access"],
            refresh=data["refresh"],
            expires=data.get("expires", 0),
            extra=extra,
        )


def generate_pkce() -> tuple[str, str]:
    """Generate PKCE verifier and challenge pair (SHA-256)."""
    verifier_bytes = secrets.token_bytes(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode("ascii")

    challenge_bytes = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(challenge_bytes).rstrip(b"=").decode("ascii")

    return verifier, challenge


def _token_path(provider: str) -> Path:
    return _TOKEN_DIR / f"{provider}.json"


def load_credentials(provider: str) -> OAuthCredentials | None:
    """Load stored credentials for a provider."""
    path = _token_path(provider)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return OAuthCredentials.from_dict(data)
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("Failed to load credentials for %s: %s", provider, e)
        return None


def save_credentials(provider: str, creds: OAuthCredentials) -> None:
    """Persist credentials to disk."""
    _TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    path = _token_path(provider)
    path.write_text(json.dumps(creds.to_dict(), indent=2))
    path.chmod(0o600)
    logger.info("Saved credentials for %s", provider)


def clear_credentials(provider: str) -> None:
    """Remove stored credentials."""
    path = _token_path(provider)
    if path.exists():
        path.unlink()
        logger.info("Cleared credentials for %s", provider)


class OAuthProvider(ABC):
    """Base class for OAuth provider implementations.

    Each subclass defines its OAuth endpoints, client credentials,
    and token exchange logic.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        ...

    @property
    @abstractmethod
    def authorize_url(self) -> str:
        ...

    @property
    @abstractmethod
    def token_url(self) -> str:
        ...

    @property
    @abstractmethod
    def client_id(self) -> str:
        ...

    @property
    @abstractmethod
    def redirect_uri(self) -> str:
        ...

    @property
    @abstractmethod
    def scopes(self) -> str:
        ...

    @property
    def oauth_enabled(self) -> bool:
        """Whether interactive OAuth login is currently supported."""
        return True

    @property
    def oauth_experimental(self) -> bool:
        """Whether the OAuth flow is known to be unstable."""
        return False

    @property
    def connection_hint(self) -> str | None:
        """Optional UI hint describing the recommended auth path."""
        return None

    @property
    def needs_local_server(self) -> bool:
        """True if redirect_uri points to localhost (OpenAI, Google).
        False if redirect goes to provider's own page (Anthropic).
        """
        return "localhost" in self.redirect_uri

    def get_stored_token(self) -> str | None:
        """Get stored access token without refreshing.

        Safe to call from any context (sync or async). Returns the token
        as-is. If it's expired, the API call will get a 401 and the
        caller should trigger a re-login.

        For async contexts that need refresh, use get_credentials_async().
        """
        creds = load_credentials(self.provider_name)
        if creds is None:
            return None
        return creds.access

    async def get_credentials_async(self) -> OAuthCredentials | None:
        """Get valid credentials, refreshing if expired. Async only."""
        creds = load_credentials(self.provider_name)
        if creds is None:
            return None
        if creds.expired:
            logger.info("Token expired for %s, refreshing...", self.provider_name)
            try:
                creds = await self._refresh_async(creds)
                save_credentials(self.provider_name, creds)
            except Exception as e:
                logger.error("Token refresh failed for %s: %s", self.provider_name, e)
                return None
        return creds

    def build_auth_url(self) -> dict[str, str]:
        """Build the OAuth authorization URL with PKCE.

        Returns {"url": auth_url, "verifier": pkce_verifier, "state": state,
                 "needs_local_server": bool, "callback_port": int | None}.
        """
        verifier, challenge = generate_pkce()
        state = secrets.token_hex(16)

        params = self._build_auth_params(challenge, state)
        url = f"{self.authorize_url}?{urlencode(params)}"

        result = {"url": url, "verifier": verifier, "state": state,
                  "needs_local_server": self.needs_local_server}
        if self.needs_local_server:
            parsed = urlparse(self.redirect_uri)
            result["callback_port"] = parsed.port
            result["callback_path"] = parsed.path
        return result

    @abstractmethod
    def _build_auth_params(self, challenge: str, state: str) -> dict[str, str]:
        ...

    async def exchange_code(self, code: str, verifier: str, state: str) -> OAuthCredentials:
        """Exchange authorization code for tokens."""
        creds = await self._exchange_code_impl(code, verifier, state)
        save_credentials(self.provider_name, creds)
        return creds

    @abstractmethod
    async def _exchange_code_impl(self, code: str, verifier: str, state: str) -> OAuthCredentials:
        ...

    @abstractmethod
    async def _refresh_async(self, creds: OAuthCredentials) -> OAuthCredentials:
        ...

    def is_logged_in(self) -> bool:
        """Check if stored credentials exist (does not validate or refresh)."""
        return load_credentials(self.provider_name) is not None

    def logout(self) -> None:
        clear_credentials(self.provider_name)

    def status(self) -> dict[str, Any]:
        """Return login status for API/UI display."""
        creds = load_credentials(self.provider_name)
        if creds is None:
            return {
                "provider": self.provider_name,
                "display_name": self.display_name,
                "logged_in": False,
                "oauth_enabled": self.oauth_enabled,
                "oauth_experimental": self.oauth_experimental,
                "connection_hint": self.connection_hint,
            }
        return {
            "provider": self.provider_name,
            "display_name": self.display_name,
            "logged_in": True,
            "expired": creds.expired,
            "expires_at": creds.expires,
            "oauth_enabled": self.oauth_enabled,
            "oauth_experimental": self.oauth_experimental,
            "connection_hint": self.connection_hint,
            "token_type": creds.extra.get("token_type", ""),
            **{k: v for k, v in creds.extra.items() if k in ("email", "accountId")},
        }

    def parse_callback_input(self, raw: str) -> tuple[str, str]:
        """Parse user-pasted or auto-captured callback into (code, state).

        Supports: full redirect URL, code#state, URLSearchParams, raw code.
        """
        raw = raw.strip()

        # Try as URL
        try:
            parsed = urlparse(raw)
            if parsed.query:
                params = parse_qs(parsed.query)
                code = params.get("code", [""])[0]
                state = params.get("state", [""])[0]
                if code:
                    return code, state
        except Exception:
            pass

        # code#state format (Anthropic)
        if "#" in raw:
            parts = raw.split("#", 1)
            return parts[0], parts[1]

        # URLSearchParams format
        if "code=" in raw:
            params = parse_qs(raw)
            code = params.get("code", [""])[0]
            state = params.get("state", [""])[0]
            if code:
                return code, state

        return raw, ""
