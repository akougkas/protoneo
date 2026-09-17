"""LLM error hygiene and classification helpers."""

from __future__ import annotations

import re

_SENSITIVE_ERROR_PATTERNS = (
    (re.compile(r'("user_id"\s*:\s*)"[^"]+"'), r'\1"[redacted]"'),
    (re.compile(r'("api_key"\s*:\s*)"[^"]+"', re.IGNORECASE), r'\1"[redacted]"'),
    (re.compile(r'("access_token"\s*:\s*)"[^"]+"', re.IGNORECASE), r'\1"[redacted]"'),
    (re.compile(r'("refresh_token"\s*:\s*)"[^"]+"', re.IGNORECASE), r'\1"[redacted]"'),
    (re.compile(r'(Bearer\s+)[A-Za-z0-9._~+/=-]+', re.IGNORECASE), r'\1[redacted]'),
    (re.compile(r'\b(sk-[A-Za-z0-9_-]{12,})\b'), "[redacted]"),
)


def sanitize_error_message(error: object) -> str:
    """Strip provider/account identifiers from UI-facing errors."""
    message = str(error)
    for pattern, replacement in _SENSITIVE_ERROR_PATTERNS:
        message = pattern.sub(replacement, message)
    return message


def served_model_mismatch(provider: str, requested: str, served: object) -> str:
    """Describe a server that answered with a model other than the one requested.

    LM Studio and similar servers may answer an unknown model name with
    whichever model is loaded, which would misattribute the output.
    """
    if not isinstance(served, str) or not served.strip() or not requested:
        return ""

    def names(value: str) -> set[str]:
        value = value.strip().lower()
        return {value, value.rsplit("/", 1)[-1], re.sub(r":\d+$", "", value)}

    wanted, got = names(requested), names(served)
    if wanted & got or any(a.startswith(b) or b.startswith(a) for a in wanted for b in got):
        return ""
    return (
        f"{provider} served model '{served}' instead of the requested '{requested}'. "
        "Load the requested model on that endpoint or choose a model it lists."
    )


def classify_model_error(error: object) -> tuple[str, str]:
    """Return ``(status, message)`` for known provider failure modes."""
    text = sanitize_error_message(error)
    if "free-models-per-day" in text:
        return "quota_limited", "OpenRouter free-model daily quota is exhausted."
    if "temporarily rate-limited upstream" in text:
        return "rate_limited", "OpenRouter upstream provider is temporarily rate-limited."
    if "code\":429" in text or " 429" in text or "rate limit" in text.lower():
        return "rate_limited", "Provider returned HTTP 429 rate limiting."
    if "Operation timed out" in text or "timed out" in text.lower():
        return "timeout", "Provider response timed out."
    return "", text
