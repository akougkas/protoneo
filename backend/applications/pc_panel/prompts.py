"""Prompt assembly for PC Panel reviewer agents."""

import yaml
from pathlib import Path


def _prompts_dir(conference_slug: str) -> Path:
    """Prompt templates live alongside this module."""
    return Path(__file__).resolve().parent / "prompts" / conference_slug


def load_prompt_pack(conference_slug: str) -> dict:
    """Load a prompt pack YAML config."""
    pack_path = _prompts_dir(conference_slug) / "prompt-pack.yaml"
    if not pack_path.exists():
        raise FileNotFoundError(f"Prompt pack not found for {conference_slug}")
    return yaml.safe_load(pack_path.read_text())


def load_shared_prompt(conference_slug: str) -> str:
    """Load the shared base prompt."""
    path = _prompts_dir(conference_slug) / "shared.md"
    return path.read_text() if path.exists() else ""


def load_role_prompt(conference_slug: str, role: str) -> str:
    """Load a role-specific prompt overlay."""
    path = _prompts_dir(conference_slug) / f"{role}.md"
    return path.read_text() if path.exists() else ""


def assemble_system_prompt(
    conference_slug: str,
    role: str,
    conference_context: str = "",
) -> str:
    """Build a complete system prompt: shared + role overlay + conference context."""
    shared = load_shared_prompt(conference_slug)
    overlay = load_role_prompt(conference_slug, role)

    parts = []
    if shared:
        parts.append(shared)
    if overlay:
        parts.append(overlay)
    if conference_context:
        parts.append(f"## Conference Context\n\n{conference_context}")

    return "\n\n---\n\n".join(parts)
