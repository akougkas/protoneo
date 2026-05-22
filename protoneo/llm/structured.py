"""Shared structured-output cleanup helpers for LLM responses."""

from __future__ import annotations

import json
import re
from typing import Any


_THINKING_BLOCK_RE = re.compile(
    r"<(?:think|thinking)\b[^>]*>[\s\S]*?</(?:think|thinking)>",
    re.IGNORECASE,
)
_OPEN_THINKING_RE = re.compile(
    r"<(?:think|thinking)\b[^>]*>[\s\S]*$",
    re.IGNORECASE,
)
_OUTPUT_TAG_RE = re.compile(r"</?(?:output|final|answer)>", re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*\n([\s\S]*?)\n?```", re.IGNORECASE)


def strip_thinking_output(content: str) -> str:
    """Remove common visible thinking wrappers from model output."""
    if not content:
        return ""
    cleaned = _THINKING_BLOCK_RE.sub("", content)
    cleaned = _OPEN_THINKING_RE.sub("", cleaned)
    return cleaned.strip()


def sanitize_structured_text(content: str) -> str:
    """Normalize text before structured JSON parsing."""
    cleaned = strip_thinking_output(content)
    cleaned = _OUTPUT_TAG_RE.sub("", cleaned)
    return cleaned.strip()


def extract_json_value(content: str) -> Any | None:
    """Best-effort JSON extraction after shared sanitizer cleanup."""
    cleaned = sanitize_structured_text(content)
    if not cleaned:
        return None

    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        pass

    for fence_match in _FENCE_RE.finditer(cleaned):
        try:
            return json.loads(fence_match.group(1).strip())
        except (json.JSONDecodeError, TypeError):
            continue

    for opening, closing in (("{", "}"), ("[", "]")):
        best = _extract_balanced_json(cleaned, opening, closing)
        if best is not None:
            return best

    return None


def extract_json_object(
    content: str,
    *,
    required_keys: set[str] | None = None,
) -> dict[str, Any] | None:
    """Extract a JSON object, optionally requiring at least one key."""
    value = extract_json_value(content)
    if isinstance(value, dict):
        if not required_keys or required_keys.intersection(value.keys()):
            return value
    return None


def _extract_balanced_json(text: str, opening: str, closing: str) -> Any | None:
    best_value = None
    best_size = 0
    pos = 0
    while pos < len(text):
        start = text.find(opening, pos)
        if start < 0:
            break
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            char = text[i]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opening:
                depth += 1
            elif char == closing:
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        value = json.loads(candidate)
                    except (json.JSONDecodeError, TypeError):
                        value = None
                    if value is not None and len(candidate) > best_size:
                        best_value = value
                        best_size = len(candidate)
                    pos = i + 1
                    break
        else:
            break
    return best_value
