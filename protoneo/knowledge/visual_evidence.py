"""VLM image-description helper.

Turns an extracted figure/table image into a provenance-bearing description
record. Reasoning-model output is sanitized before reviewers see it.
"""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("protoneo.knowledge.visual_evidence")

_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_OPEN_THINK_RE = re.compile(r"<think>.*\Z", re.IGNORECASE | re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_NUMERIC_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:x|%|×|ms|us|s|gb|mb|tb|gflops?|tflops?|pflops?|nodes?|gpus?|cores?|speedup)\b",
    re.IGNORECASE,
)


def sanitize_description(text: str) -> str:
    """Remove reasoning scratchpad and markdown noise from a VLM description."""
    if not text:
        return ""
    text = _THINK_RE.sub(" ", text)
    text = _OPEN_THINK_RE.sub(" ", text)
    text = _HEADING_RE.sub("", text)
    text = text.replace("**", "").replace("`", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_numeric_claims(text: str, limit: int = 12) -> list[str]:
    """Extract short quantitative claims as windowed snippets."""
    claims: list[str] = []
    seen: set[str] = set()
    for match in _NUMERIC_RE.finditer(text):
        start = max(0, match.start() - 24)
        end = min(len(text), match.end() + 24)
        snippet = text[start:end].strip()
        key = match.group(0).lower()
        if key not in seen:
            seen.add(key)
            claims.append(snippet)
        if len(claims) >= limit:
            break
    return claims


def _encode_data_url(image_path: str) -> str:
    data = Path(image_path).read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def describe_image(
    image_path: str,
    vlm_config: dict[str, Any],
    kind: str = "figure",
    caption: str = "",
) -> dict[str, Any]:
    """Describe one image with an OpenAI-compatible VLM.

    Failures are returned as provenance records instead of raising, so parsing
    can continue without silently losing why vision grounding was unavailable.
    """
    url = str(vlm_config["url"])
    if not url.endswith("/chat/completions"):
        url = url.rstrip("/") + "/chat/completions"
    model = str(vlm_config.get("model", ""))
    prompt = vlm_config.get("prompt") or (
        "Describe this scientific figure or table for a paper reviewer in 4-6 sentences. "
        "State the chart/table type, axes or columns, compared methods, and key numeric "
        "results. Plain text only, no markdown, no reasoning."
    )
    effective_prompt = f"{prompt}\nManuscript caption: {caption}" if caption else prompt

    record: dict[str, Any] = {
        "kind": kind,
        "image_path": image_path,
        "caption": caption,
        "model": model,
        "endpoint": url,
        "prompt": prompt,
        "description": "",
        "description_source": "none",
        "numeric_claims": [],
        "confidence": 0.0,
        "grounding": "visual",
        "error": "",
    }
    try:
        payload = {
            "model": model,
            "temperature": vlm_config.get("temperature", 0.1),
            "top_p": vlm_config.get("top_p", 0.9),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": effective_prompt},
                        {"type": "image_url", "image_url": {"url": _encode_data_url(image_path)}},
                    ],
                }
            ],
        }
        response = httpx.post(url, json=payload, timeout=vlm_config.get("timeout", 120.0))
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        description = sanitize_description(raw if isinstance(raw, str) else str(raw))
        record["description"] = description
        record["description_source"] = "vlm" if description else "empty"
        record["numeric_claims"] = extract_numeric_claims(description)
        if description:
            record["confidence"] = round(
                min(
                    1.0,
                    0.4
                    + 0.1 * len(record["numeric_claims"])
                    + (0.2 if len(description) > 80 else 0.0),
                ),
                2,
            )
    except Exception as exc:  # noqa: BLE001
        record["error"] = str(exc)
        record["description_source"] = "error"
        logger.warning("VLM describe_image failed for %s: %s", image_path, exc)
    return record
