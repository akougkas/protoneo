"""
PC Panel review session orchestration.

Wires conference profiles, prompts, and the kernel deliberation
engine into a complete review workflow.
"""

import json
import logging
import re
from typing import Any

from protoneo.agents.types import Document
from protoneo.config.schema import AgentConfig, DeliberationConfig, PhaseConfig
from protoneo.deliberation.types import DeliberationResult
from .conference import ConferenceProfile
from .prompts import assemble_system_prompt
from .schemas import (
    DeliberationRound,
    IndividualReview,
    MetaReview,
    ReviewerProvenance,
    ReviewPacket,
)

logger = logging.getLogger("protoneo.pc_panel.review")

# Provider preferences for automatic model assignment.
# When multiple providers are available, each role gets the first match.
# Roles not listed here fall through to the first available model.
# Uses generic provider names only (no site-specific node names).
_ROLE_PROVIDER_PREFS: dict[str, list[str]] = {
    "technical": ["anthropic", "openai"],
    "systems": ["anthropic", "openai"],
    "novelty": ["openai", "anthropic"],
    "clarity": ["openai", "anthropic"],
    "skeptic": ["anthropic", "openai"],
    "meta": ["anthropic", "openai"],
}

# Per-role inference parameter defaults.
# Analytical roles (skeptic, artifact, systems, technical) get low temperature
# for deterministic, grounded verification. Creative roles (novelty, clarity,
# meta) get higher temperature for lateral synthesis.
_ROLE_INFERENCE_PREFS: dict[str, dict[str, float]] = {
    "technical": {"temperature": 0.15, "top_p": 0.85},
    "systems": {"temperature": 0.15, "top_p": 0.85},
    "skeptic": {"temperature": 0.15, "top_p": 0.85},
    "artifact": {"temperature": 0.15, "top_p": 0.85},
    "novelty": {"temperature": 0.85, "top_p": 0.95, "presence_penalty": 0.2},
    "clarity": {"temperature": 0.85, "top_p": 0.95, "presence_penalty": 0.2},
    "meta": {"temperature": 0.85, "top_p": 0.95, "presence_penalty": 0.2},
}


def _resolve_default_models(roles: list[str] | None = None) -> dict[str, str]:
    """Build default model assignments from settings active_models.

    Returns a dict mapping role names to provider-prefixed model IDs.
    Roles without explicit provider preferences get the first available model.
    """
    try:
        from protoneo.llm.settings import load_settings
        settings = load_settings()
        active = settings.active_models or {}
        if not active:
            return {}

        available = {prov: f"{prov}/{mid}" for prov, mid in active.items() if mid}
        if not available:
            return {}

        first_model = next(iter(available.values()))
        target_roles = roles or list(_ROLE_PROVIDER_PREFS.keys())

        result = {}
        for role in target_roles:
            prefs = _ROLE_PROVIDER_PREFS.get(role, [])
            assigned = False
            for prov in prefs:
                if prov in available:
                    result[role] = available[prov]
                    assigned = True
                    break
            if not assigned:
                result[role] = first_model
        return result
    except Exception as e:
        logger.warning("Failed to resolve default models from settings: %s", e)
        return {}


def strip_json_fences(text: str) -> str:
    """Strip markdown code fences from JSON output.

    Handles ```json ... ```, ``` ... ```, and leading/trailing whitespace.
    """
    stripped = text.strip()
    # Remove opening fence
    stripped = re.sub(r"^```(?:json)?\s*\n?", "", stripped)
    # Remove closing fence
    stripped = re.sub(r"\n?\s*```\s*$", "", stripped)
    return stripped.strip()


def _extract_json(text: str) -> dict | None:
    """Try to extract a JSON object from LLM output."""
    # Fix 5: Always strip fences before attempting parse
    cleaned = strip_json_fences(text)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r"```(?:json)?\s*\n([\s\S]*?)\n```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    brace_start = cleaned.find("{")
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[brace_start : i + 1])
                    except (json.JSONDecodeError, TypeError):
                        break
    return None


def _reviewer_roles_from_profile(
    profile: ConferenceProfile,
    include_optional: bool = False,
) -> list[str]:
    """Extract reviewer role IDs from a conference profile.

    Returns all panel agent keys except meta_reviewer. If include_optional
    is True, also includes optional agents (artifact, scalability, etc.).
    """
    roles = [
        key for key in profile.panel_agents
        if key != "meta_reviewer"
    ]
    if include_optional:
        roles.extend(profile.optional_agents.keys())
    return roles


def build_agent_configs(
    profile: ConferenceProfile,
    conference_slug: str,
    model_map: dict[str, str] | None = None,
    include_artifact: bool = False,
    user_instructions: str = "",
) -> dict[str, AgentConfig]:
    """Build AgentConfig instances from a conference profile and prompts.

    The profile is the single source of truth for which agents to create.
    Agent roles, focus areas, and review guidelines are all profile-driven.
    """
    reviewer_roles = _reviewer_roles_from_profile(profile)

    if include_artifact and "artifact" in profile.optional_agents:
        reviewer_roles.append("artifact")

    all_roles = reviewer_roles + ["meta"]
    defaults = _resolve_default_models(roles=all_roles)
    models = {**defaults, **(model_map or {})}

    conference_context = (
        f"Conference: {profile.short_name} ({profile.name})\n"
        f"Location: {profile.location}\n"
        f"Dates: {profile.dates}\n"
        f"Scope: {profile.scope_text()}\n"
        f"Dual-anonymous: {profile.dual_anonymous}\n"
        f"Max pages: {profile.max_pages} (excluding references)\n"
        f"Format: {profile.format_style}\n"
        f"Merit scale: {profile.merit_labels()}\n"
        f"Expertise scale: {profile.expertise_labels()}"
    )
    if user_instructions:
        conference_context += f"\n\n## PC Chair Instructions\n\n{user_instructions}"

    configs: dict[str, AgentConfig] = {}

    for role in reviewer_roles:
        agent_def = profile.panel_agents.get(
            role, profile.optional_agents.get(role)
        )
        focus = ", ".join(agent_def.focus) if agent_def else role
        prompt = assemble_system_prompt(
            conference_slug, role, conference_context, agent_focus=focus,
        )
        inference = _ROLE_INFERENCE_PREFS.get(role, {})
        configs[role] = AgentConfig(
            role=agent_def.role if agent_def else role.replace("_", " ").title() + " Reviewer",
            model=models.get(role, models.get("technical", "")),
            system_prompt=prompt,
            focus=focus,
            max_tokens=16384,
            temperature=inference.get("temperature"),
            top_p=inference.get("top_p"),
            presence_penalty=inference.get("presence_penalty"),
            frequency_penalty=inference.get("frequency_penalty"),
        )

    # Meta-reviewer is always present.
    # Frontend sends model_map with key "meta_reviewer" (profile agent ID),
    # while internal config uses key "meta". Check both.
    meta_def = profile.panel_agents.get("meta_reviewer")
    meta_focus_text = meta_def.focus[0] if meta_def and meta_def.focus else "synthesis, consensus analysis, final recommendation"
    meta_prompt = assemble_system_prompt(
        conference_slug, "meta", conference_context, agent_focus=meta_focus_text,
    )
    meta_model = (models.get("meta_reviewer")
                  or models.get("meta")
                  or models.get("technical", ""))
    meta_inference = _ROLE_INFERENCE_PREFS.get("meta", {})
    configs["meta"] = AgentConfig(
        role=meta_def.role if meta_def else "Meta-Reviewer",
        model=meta_model,
        system_prompt=meta_prompt,
        focus=meta_focus_text,
        max_tokens=16384,
        temperature=meta_inference.get("temperature"),
        top_p=meta_inference.get("top_p"),
        presence_penalty=meta_inference.get("presence_penalty"),
        frequency_penalty=meta_inference.get("frequency_penalty"),
    )

    return configs


def build_deliberation_config(
    reviewer_ids: list[str] | None = None,
    max_rounds: int = 2,
) -> DeliberationConfig:
    """Build the standard 3-phase deliberation config for PC Panel.

    When reviewer_ids is None, falls back to HPDC26 defaults for backward
    compatibility. Callers should pass the actual reviewer IDs from the profile.
    """
    if reviewer_ids is None:
        reviewer_ids = ["technical", "novelty", "clarity", "skeptic"]
    return DeliberationConfig(
        pattern="independent_synthesis",
        phases=[
            PhaseConfig(
                name="independent_review",
                mode="parallel",
                agents=reviewer_ids,
            ),
            PhaseConfig(
                name="deliberation",
                mode="round_robin",
                agents=reviewer_ids,
                max_rounds=max_rounds,
                visibility="open",
            ),
            PhaseConfig(
                name="meta_review",
                mode="sequential",
                agents=["meta"],
                input="all_prior_outputs",
            ),
        ],
    )


def build_user_message(document: Document, profile: ConferenceProfile) -> str:
    """Construct the user message from the paper text and conference context."""
    header = (
        f"You are reviewing a submission to {profile.short_name} "
        f"({profile.name}).\n\n"
        f"Paper type requirements: {profile.max_pages} pages max "
        f"(excluding references), {profile.format_style} format.\n"
        f"Dual-anonymous review: {'Yes' if profile.dual_anonymous else 'No'}.\n\n"
        f"Please review the following manuscript and return your assessment "
        f"as a JSON object matching the output contract in your instructions.\n\n"
        f"{'=' * 60}\n"
        f"MANUSCRIPT\n"
        f"{'=' * 60}\n\n"
    )
    paper_content = document.markdown if document.markdown else document.text
    return header + paper_content


def parse_review_output(
    output,
    role: str,
    agent_config: AgentConfig | None = None,
    prompt_pack_version: str = "",
) -> IndividualReview:
    """Parse an agent output into a structured IndividualReview."""
    parsed = _extract_json(output.content)
    model = output.metadata.get("model", "")

    # Build per-reviewer provenance from agent config
    provenance = ReviewerProvenance(
        model_id=model,
        temperature=output.metadata.get("temperature") if agent_config is None else agent_config.temperature,
        top_p=agent_config.top_p if agent_config else None,
        presence_penalty=agent_config.presence_penalty if agent_config else None,
        frequency_penalty=agent_config.frequency_penalty if agent_config else None,
        prompt_pack_version=prompt_pack_version,
    )

    if parsed:
        return IndividualReview(
            reviewer_role=role,
            agent_id=output.agent_id,
            model=model,
            summary=parsed.get("summary", ""),
            overall_merit=parsed.get("overall_merit", {}),
            expertise=parsed.get("expertise", {}),
            strengths=parsed.get("strengths", []),
            weaknesses=parsed.get("weaknesses", []),
            questions_for_authors=parsed.get("questions_for_authors", []),
            comments_for_authors=parsed.get("comments_for_authors", ""),
            internal_committee_concerns=parsed.get(
                "internal_committee_concerns", []
            ),
            confidence=parsed.get("confidence", {}),
            revision_actions=parsed.get("revision_actions", []),
            citations=parsed.get("citations", []),
            raw_content=output.content,
            provenance=provenance,
        )

    return IndividualReview(
        reviewer_role=role,
        agent_id=output.agent_id,
        model=model,
        comments_for_authors=output.content,
        raw_content=output.content,
        provenance=provenance,
    )


def _validate_score_distribution(scores: dict, max_score: int = 5) -> dict:
    """Validate and clean score_distribution from meta-review.

    Clamps scores to the valid merit scale range and strips unknown reviewer keys.
    """
    if not isinstance(scores, dict):
        return {}
    cleaned = {}
    for key, val in scores.items():
        if isinstance(val, (int, float)):
            clamped = max(1, min(int(val), max_score))
            cleaned[key] = clamped
    return cleaned


def parse_meta_review(output) -> MetaReview:
    """Parse the meta-reviewer output."""
    parsed = _extract_json(output.content)

    if parsed:
        # Fix 13: Validate score_distribution to prevent hallucinated values
        raw_scores = parsed.get("score_distribution", {})
        validated_scores = _validate_score_distribution(raw_scores)
        return MetaReview(
            panel_summary=parsed.get("panel_summary", ""),
            score_distribution=validated_scores,
            consensus=parsed.get("consensus", {}),
            agreements=parsed.get("agreements", []),
            disagreements=parsed.get("disagreements", []),
            final_recommendation=parsed.get("final_recommendation", {}),
            confidence=parsed.get("confidence", {}),
            decision_risk_notes=parsed.get("decision_risk_notes", []),
            author_facing_summary=parsed.get("author_facing_summary", ""),
            prioritized_revision_plan=parsed.get(
                "prioritized_revision_plan", []
            ),
            submission_readiness=parsed.get("submission_readiness", {}),
            raw_content=output.content,
        )

    return MetaReview(
        author_facing_summary=output.content,
        raw_content=output.content,
    )


def result_to_packet(
    result: DeliberationResult,
    profile: ConferenceProfile,
    paper_title: str = "",
    final_review: dict | None = None,
    agent_configs: dict[str, AgentConfig] | None = None,
    prompt_pack_version: str = "",
) -> ReviewPacket:
    """Convert a DeliberationResult into a structured ReviewPacket."""
    reviews: list[IndividualReview] = []
    deliberation_rounds: list[DeliberationRound] = []
    meta = MetaReview()

    # Derive role keys from the profile so any conference works
    role_keys = _reviewer_roles_from_profile(profile, include_optional=True)

    _agent_configs = agent_configs or {}

    for phase in result.phases:
        if phase.phase_name == "independent_review":
            for output in phase.outputs:
                # Match agent_id against known role keys from the profile
                role_guess = "unknown"
                for r in role_keys:
                    if r in output.agent_id:
                        role_guess = r
                        break
                reviews.append(parse_review_output(
                    output, role_guess,
                    agent_config=_agent_configs.get(role_guess),
                    prompt_pack_version=prompt_pack_version,
                ))

        elif phase.phase_name == "deliberation":
            current_round = 0
            round_entries: list[dict] = []
            for output in phase.outputs:
                rnd = output.metadata.get("round", 1)
                if rnd != current_round:
                    if round_entries:
                        deliberation_rounds.append(
                            DeliberationRound(
                                round_number=current_round,
                                entries=round_entries,
                            )
                        )
                    current_round = rnd
                    round_entries = []
                round_entries.append(
                    {
                        "agent_id": output.agent_id,
                        "role": output.agent_role,
                        "content": output.content,
                    }
                )
            if round_entries:
                deliberation_rounds.append(
                    DeliberationRound(
                        round_number=current_round,
                        entries=round_entries,
                    )
                )

        elif phase.phase_name == "meta_review":
            if phase.outputs:
                meta = parse_meta_review(phase.outputs[0])

    # Build packet-level provenance for reproducibility
    provenance: dict[str, Any] = {
        "prompt_pack_version": prompt_pack_version,
        "conference_slug": profile.slug,
        "graph_pruning_threshold": profile.graph_pruning_threshold,
        "agents": {},
    }
    for aid, cfg in _agent_configs.items():
        provenance["agents"][aid] = {
            "model_id": cfg.model,
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
            "presence_penalty": cfg.presence_penalty,
            "frequency_penalty": cfg.frequency_penalty,
        }

    return ReviewPacket(
        session_id=result.session_id,
        conference=profile.slug,
        paper_title=paper_title,
        reviews=reviews,
        deliberation=deliberation_rounds,
        meta_review=meta,
        pc_chair_review=final_review or {},
        duration_seconds=result.duration_seconds,
        total_cost=result.total_cost,
        provenance_metadata=provenance,
    )
