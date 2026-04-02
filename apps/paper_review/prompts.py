"""Prompt assembly for Paper Review reviewer agents."""

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


def load_pc_chair_prompt(conference_slug: str) -> str:
    """Load the PC Chair prompt for a venue.

    Returns the venue-specific pc_chair.md content, or empty string
    if no PC Chair prompt exists for this venue.
    """
    path = _prompts_dir(conference_slug) / "pc_chair.md"
    return path.read_text() if path.exists() else ""


def assemble_system_prompt(
    conference_slug: str,
    role: str,
    conference_context: str = "",
    agent_focus: str = "",
) -> str:
    """Build a complete system prompt: shared + role overlay + focus anchor + conference context."""
    shared = load_shared_prompt(conference_slug)
    overlay = load_role_prompt(conference_slug, role)

    parts = []
    if shared:
        parts.append(shared)
    if overlay:
        parts.append(overlay)

    # Epistemic anchor: inject domain expertise directive between role overlay
    # and conference context so the agent evaluates through its focus lens.
    if agent_focus:
        parts.append(
            "### CRITICAL EPISTEMIC DIRECTIVE\n\n"
            f"Your specific domain expertise for this panel is: [{agent_focus}]. "
            "You must aggressively anchor your technical audit, critique, and "
            "Knowledge Graph traversal strictly on these concepts. If the paper's "
            "core claims align with your focus, you are the primary authority; "
            "delegate other domains to your co-reviewers."
        )

    if conference_context:
        parts.append(f"## Conference Context\n\n{conference_context}")

    return "\n\n---\n\n".join(parts)
