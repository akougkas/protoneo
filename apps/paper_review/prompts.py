"""Prompt assembly for Paper Review reviewer agents."""

import re
from pathlib import Path
from typing import Any

import yaml


_THINKING_BLOCK_RE = re.compile(
    r"<(?:think|thinking)\b[^>]*>[\s\S]*?</(?:think|thinking)>",
    re.IGNORECASE,
)
_LEADING_REASONING_RE = re.compile(
    r"^\s*(?:chain[-_\s]*of[-_\s]*thought|reasoning|analysis|scratchpad)\s*:",
    re.IGNORECASE,
)
_FINAL_MARKER_RE = re.compile(
    r"(?ims)^\s*(?:final(?:\s+(?:answer|review))?|answer|review)\s*:\s*(.+)$"
)


def _prompts_dir(conference_slug: str) -> Path:
    """Prompt templates live alongside this module."""
    return Path(__file__).resolve().parent / "prompts" / conference_slug


def load_prompt_pack(conference_slug: str) -> dict:
    """Load a prompt pack YAML config."""
    pack_path = _prompts_dir(conference_slug) / "prompt-pack.yaml"
    if not pack_path.exists():
        raise FileNotFoundError(f"Prompt pack not found for {conference_slug}")
    return yaml.safe_load(pack_path.read_text())


def load_prompt_guardrails(conference_slug: str) -> dict[str, Any]:
    """Return guardrail metadata declared by the venue prompt pack."""
    pack = load_prompt_pack(conference_slug)
    guardrails = pack.get("guardrails", {})
    return guardrails if isinstance(guardrails, dict) else {}


def prompt_pack_no_chain_of_thought(conference_slug: str) -> bool:
    """Whether the venue prompt pack forbids chain-of-thought output."""
    try:
        return bool(load_prompt_guardrails(conference_slug).get("no_chain_of_thought"))
    except FileNotFoundError:
        return False


def strip_chain_of_thought(text: str) -> str:
    """Remove common private-reasoning wrappers from LLM output.

    Prompt packs already instruct reviewers not to reveal chain-of-thought. This
    cleanup enforces that metadata at parse time without changing normal review
    prose or JSON payloads.
    """
    if not text:
        return ""

    cleaned = _THINKING_BLOCK_RE.sub("", text).strip()
    if not _LEADING_REASONING_RE.match(cleaned):
        return cleaned

    # If the model leaked a scratchpad before the structured payload, preserve
    # only the JSON object so the normal parser can still validate it.
    first_json = cleaned.find("{")
    if first_json >= 0:
        return cleaned[first_json:].strip()

    final_match = _FINAL_MARKER_RE.search(cleaned)
    if final_match:
        return final_match.group(1).strip()

    return ""


def apply_output_guardrails(
    text: str,
    *,
    no_chain_of_thought: bool = False,
    guardrails: dict[str, Any] | None = None,
) -> str:
    """Apply prompt-pack output guardrails to model text."""
    active_no_cot = no_chain_of_thought or bool(
        guardrails and guardrails.get("no_chain_of_thought")
    )
    if active_no_cot:
        return strip_chain_of_thought(text)
    return text


def load_shared_prompt(conference_slug: str) -> str:
    """Load the shared base prompt."""
    path = _prompts_dir(conference_slug) / "shared.md"
    return path.read_text() if path.exists() else ""


def load_role_prompt(conference_slug: str, role: str) -> str:
    """Load a role-specific prompt overlay."""
    path = _prompts_dir(conference_slug) / f"{role}.md"
    return path.read_text() if path.exists() else ""


def load_pc_chair_prompt(conference_slug: str) -> str:
    """Load a venue final-synthesis prompt.

    The current pipeline uses meta.md as the single Meta-Reviewer/PC Chair
    synthesis prompt. Older callers may still ask for pc_chair.md, so prefer
    meta.md and fall back to a venue pc_chair.md only for legacy prompt packs.
    """
    meta = load_role_prompt(conference_slug, "meta")
    if meta:
        return meta
    path = _prompts_dir(conference_slug) / "pc_chair.md"
    if path.exists():
        return path.read_text()
    return ""


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
