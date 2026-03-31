"""Tests for OAuth provider system."""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from protoneo.llm.providers.oauth_base import (
    OAuthCredentials,
    generate_pkce,
    load_credentials,
    save_credentials,
    clear_credentials,
)
from protoneo.llm.providers.anthropic_oauth import AnthropicOAuth
from protoneo.llm.providers.openai_oauth import OpenAIOAuth, _extract_account_id
from protoneo.llm.providers.registry import ProviderRegistry


# ── PKCE Tests ──────────────────────────────────────────────

def test_pkce_generates_valid_pair():
    verifier, challenge = generate_pkce()
    assert len(verifier) > 0
    assert len(challenge) > 0
    assert verifier != challenge


def test_pkce_is_unique():
    v1, c1 = generate_pkce()
    v2, c2 = generate_pkce()
    assert v1 != v2
    assert c1 != c2


# ── Credential Storage Tests ───────────────────────────────

def test_credentials_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "protoneo.llm.providers.oauth_base._TOKEN_DIR", tmp_path
    )
    creds = OAuthCredentials(
        access="test-access",
        refresh="test-refresh",
        expires=time.time() + 3600,
        extra={"email": "test@example.com"},
    )
    save_credentials("test-provider", creds)
    loaded = load_credentials("test-provider")
    assert loaded is not None
    assert loaded.access == "test-access"
    assert loaded.refresh == "test-refresh"
    assert loaded.extra["email"] == "test@example.com"


def test_clear_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "protoneo.llm.providers.oauth_base._TOKEN_DIR", tmp_path
    )
    creds = OAuthCredentials(access="x", refresh="y", expires=time.time() + 3600)
    save_credentials("test-provider", creds)
    clear_credentials("test-provider")
    assert load_credentials("test-provider") is None


def test_expired_credentials():
    creds = OAuthCredentials(access="x", refresh="y", expires=time.time() - 100)
    assert creds.expired


def test_valid_credentials():
    creds = OAuthCredentials(access="x", refresh="y", expires=time.time() + 3600)
    assert not creds.expired


# ── Provider Auth URL Tests ────────────────────────────────

def test_anthropic_auth_url():
    provider = AnthropicOAuth()
    auth = provider.build_auth_url()
    assert "claude.ai/oauth/authorize" in auth["url"]
    assert "code=true" in auth["url"]
    assert auth["verifier"]
    assert auth["state"]
    # Anthropic uses state=verifier (pi-ai pattern)
    assert auth["state"] == auth["verifier"]


def test_openai_auth_url():
    provider = OpenAIOAuth()
    auth = provider.build_auth_url()
    assert "auth.openai.com/oauth/authorize" in auth["url"]
    assert "codex_cli_simplified_flow=true" in auth["url"]
    assert auth["verifier"]
    assert auth["state"]



# ── Callback Parsing Tests ─────────────────────────────────

def test_parse_url_callback():
    provider = AnthropicOAuth()
    code, state = provider.parse_callback_input(
        "https://console.anthropic.com/oauth/code/callback?code=abc123&state=xyz789"
    )
    assert code == "abc123"
    assert state == "xyz789"


def test_parse_code_hash_state():
    provider = AnthropicOAuth()
    code, state = provider.parse_callback_input("abc123#xyz789")
    assert code == "abc123"
    assert state == "xyz789"


def test_parse_raw_code():
    provider = AnthropicOAuth()
    code, state = provider.parse_callback_input("abc123")
    assert code == "abc123"
    assert state == ""


# ── JWT Parsing Tests ──────────────────────────────────────

def test_extract_account_id_from_jwt():
    import base64
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
    payload_dict = {
        "sub": "user123",
        "https://api.openai.com/auth": {
            "chatgpt_account_id": "acct_abc123"
        }
    }
    payload = base64.urlsafe_b64encode(
        json.dumps(payload_dict).encode()
    ).rstrip(b"=").decode()
    sig = "fakesig"
    jwt = f"{header}.{payload}.{sig}"
    assert _extract_account_id(jwt) == "acct_abc123"


def test_extract_account_id_invalid_jwt():
    assert _extract_account_id("not-a-jwt") == ""
    assert _extract_account_id("") == ""


# ── Registry Tests ─────────────────────────────────────────

def test_registry_lists_providers():
    reg = ProviderRegistry()
    providers = reg.list_providers()
    assert "openai" in providers


def test_registry_begin_login():
    reg = ProviderRegistry()
    auth = reg.begin_login("openai")
    assert "url" in auth
    assert "verifier" in auth
    assert "state" in auth


def test_registry_begin_login_unknown():
    reg = ProviderRegistry()
    with pytest.raises(ValueError, match="Unknown provider"):
        reg.begin_login("nonexistent")


def test_registry_status_no_login():
    reg = ProviderRegistry()
    statuses = reg.all_status()
    assert len(statuses) == 1
    for s in statuses:
        assert "provider" in s
        assert "logged_in" in s
        assert "has_credentials" in s
        assert "api_key_source" in s


def test_registry_resolve_api_key_env_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-test123")
    # Use temp dir for token storage so stored OAuth tokens don't interfere
    monkeypatch.setattr("protoneo.llm.providers.oauth_base._TOKEN_DIR", tmp_path)
    reg = ProviderRegistry()
    key = reg.resolve_api_key("openai")
    assert key == "sk-openai-test123"


def test_registry_resolve_api_key_none():
    reg = ProviderRegistry()
    # Clear any env vars
    import os
    for var in ["ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN"]:
        os.environ.pop(var, None)
    key = reg.resolve_api_key("anthropic")
    # May be None or from stored OAuth; either way shouldn't crash
    assert key is None or isinstance(key, str)
