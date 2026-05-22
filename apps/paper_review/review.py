"""
Paper Review session orchestration.

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
from .prompts import (
    apply_output_guardrails,
    assemble_system_prompt,
    prompt_pack_no_chain_of_thought,
)
from .schemas import (
    DeliberationRound,
    IndividualReview,
    MetaReview,
    ReviewerProvenance,
    ReviewPacket,
    sanitize_final_review,
)

logger = logging.getLogger("protoneo.paper_review.review")

# Provider preferences for automatic model assignment.  Local review defaults
# deliberately split the panel: Dynamo is the deterministic analytical endpoint;
# Mini is the more exploratory adversarial endpoint.
_ROLE_PROVIDER_PREFS: dict[str, list[str]] = {
    "technical": ["lan-dynamo", "openai"],
    "systems": ["lan-dynamo", "openai"],
    "clarity": ["lan-dynamo", "openai"],
    "meta": ["lan-dynamo", "openai"],
    "novelty": ["lan-mini", "lan-dynamo", "openai"],
    "skeptic": ["lan-mini", "lan-dynamo", "openai"],
    "artifact": ["lan-mini", "lan-dynamo", "openai"],
}

_GRAPH_STEPS = {"ontology", "extraction", "coref", "verification"}
_SUBSCRIPTION_PROVIDERS = {"openai"}
_GRAPH_PROVIDER_PREFS = [
    "lan-dynamo",
    "localhost-dynamo",
    "localhost-lmstudio",
    "local",
    "ollama",
    "lmstudio",
    "lan-mini",
    "localhost-ollama",
]

# Per-role inference parameter defaults. These are role-level targets; local
# endpoint sampler profiles below add request-side top_k/min_p/repeat_penalty.
_ROLE_INFERENCE_PREFS: dict[str, dict[str, float]] = {
    "technical": {"temperature": 0.2, "top_p": 0.9},
    "systems": {"temperature": 0.2, "top_p": 0.9},
    "skeptic": {"temperature": 0.45, "top_p": 0.95},
    "artifact": {"temperature": 0.35, "top_p": 0.95},
    "novelty": {"temperature": 0.4, "top_p": 0.95},
    "clarity": {"temperature": 0.18, "top_p": 0.85},
    "meta": {"temperature": 0.25, "top_p": 0.9},
}

_LOCAL_ENDPOINT_INFERENCE_PREFS: dict[str, dict[str, float | int | str]] = {
    # Analytical, fast, deterministic: tight nucleus, smaller top-k, modest
    # repeat penalty to keep review JSON stable and evidence-focused.
    "lan-dynamo": {
        "temperature": 0.15,
        "top_p": 0.82,
        "top_k": 20,
        "min_p": 0.05,
        "repeat_penalty": 1.05,
    },
    # Creative/adversarial: wider sampler for novelty/skeptic exploration while
    # still bounded enough for JSON review forms.
    "lan-mini": {
        "temperature": 0.45,
        "top_p": 0.95,
        "top_k": 80,
        "min_p": 0.02,
        "repeat_penalty": 1.08,
    },
}


def _provider_from_model(model_id: str) -> str:
    return model_id.split("/", 1)[0] if "/" in model_id else ""


def is_local_model_id(model_id: str, settings: Any | None = None) -> bool:
    """Return True when a provider-prefixed model resolves to a local endpoint."""
    provider = _provider_from_model(model_id)
    if not provider or provider in _SUBSCRIPTION_PROVIDERS:
        return False

    local_providers: set[str] = set()
    if settings is not None:
        local_providers = {
            e.id for e in (
                list(getattr(settings, "localhost_endpoints", []))
                + list(getattr(settings, "lan_endpoints", []))
            )
        }

    return (
        provider in local_providers
        or provider.startswith("lan-")
        or provider.startswith("localhost-")
        or provider in {"local", "ollama", "lmstudio"}
    )


def _load_settings_context() -> tuple[Any | None, dict[str, str]]:
    try:
        from protoneo.llm.settings import load_settings, resolve_preset

        settings = load_settings()
        preset = (
            resolve_preset(settings.active_preset, settings)
            if settings.active_preset
            else None
        )
        assignments = dict(preset.assignments) if preset else {}
        return settings, assignments
    except Exception as e:
        logger.warning("Failed to resolve paper review model settings: %s", e)
        return None, {}


def _active_models(settings: Any | None, *, require_local: bool = False) -> dict[str, str]:
    if settings is None:
        return {}
    models = {}
    for provider, model_id in (settings.active_models or {}).items():
        candidate = f"{provider}/{model_id}" if model_id else ""
        if candidate and (not require_local or is_local_model_id(candidate, settings)):
            models[provider] = candidate
    return models


def resolve_paper_review_model(
    key: str,
    model_map: dict[str, str] | None = None,
    *,
    fallback_keys: tuple[str, ...] = (),
    require_local: bool = False,
) -> str:
    """Resolve one paper-review model assignment.

    Resolution order:
    1. explicit request model_map
    2. active preset assignment
    3. role/provider preference fallback over active_models
    4. first acceptable active model
    """
    settings, preset_assignments = _load_settings_context()

    def acceptable(candidate: str | None) -> str:
        if not candidate:
            return ""
        if require_local and not is_local_model_id(candidate, settings):
            return ""
        return candidate

    lookup_keys = (key, *fallback_keys)
    explicit = model_map or {}
    for lookup_key in lookup_keys:
        candidate = acceptable(explicit.get(lookup_key))
        if candidate:
            return candidate

    for lookup_key in lookup_keys:
        candidate = acceptable(preset_assignments.get(lookup_key))
        if candidate:
            return candidate

    available = _active_models(settings, require_local=require_local)
    if not available:
        return ""

    prefs = (
        _GRAPH_PROVIDER_PREFS
        if require_local or key in _GRAPH_STEPS
        else _ROLE_PROVIDER_PREFS.get(key, [])
    )
    for provider in prefs:
        if provider in available:
            return available[provider]

    return next(iter(available.values()), "")


def _inference_for(role: str, model_id: str) -> dict[str, float | int | str]:
    """Merge role calibration with request-side local endpoint sampling."""
    merged: dict[str, float | int | str] = dict(_ROLE_INFERENCE_PREFS.get(role, {}))
    provider = _provider_from_model(model_id)
    if provider in _LOCAL_ENDPOINT_INFERENCE_PREFS:
        merged.update(_LOCAL_ENDPOINT_INFERENCE_PREFS[provider])
        if role == "meta":
            # Final synthesis must remain tight even when routed locally.
            merged["temperature"] = 0.25
            merged["top_p"] = 0.9
            merged["top_k"] = min(int(merged.get("top_k", 40)), 40)
        elif role in {"technical", "systems", "clarity"} and provider == "lan-dynamo":
            merged["temperature"] = min(float(merged.get("temperature", 0.2)), 0.18)
        elif role in {"skeptic", "novelty", "artifact"} and provider == "lan-mini":
            merged["temperature"] = max(float(merged.get("temperature", 0.4)), 0.45)
    return merged


def _resolve_default_models(roles: list[str] | None = None) -> dict[str, str]:
    """Build default model assignments from active preset and active_models.

    Returns a dict mapping role names to provider-prefixed model IDs. The active
    preset is authoritative when it assigns a role; active_models only provide
    safe role/provider fallbacks.
    """
    target_roles = roles or list(_ROLE_PROVIDER_PREFS.keys())
    result: dict[str, str] = {}
    for role in target_roles:
        fallback_keys = ("meta_reviewer",) if role == "meta" else ()
        model = resolve_paper_review_model(role, fallback_keys=fallback_keys)
        if model:
            result[role] = model
    return result


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
        conference_context += f"\n\n## Review Chair Instructions\n\n{user_instructions}"

    configs: dict[str, AgentConfig] = {}

    for role in reviewer_roles:
        agent_def = profile.panel_agents.get(
            role, profile.optional_agents.get(role)
        )
        focus = ", ".join(agent_def.focus) if agent_def else role
        prompt = assemble_system_prompt(
            conference_slug, role, conference_context, agent_focus=focus,
        )
        model_id = models.get(role, models.get("technical", ""))
        inference = _inference_for(role, model_id)
        configs[role] = AgentConfig(
            role=agent_def.role if agent_def else role.replace("_", " ").title() + " Reviewer",
            model=model_id,
            system_prompt=prompt,
            focus=focus,
            max_tokens=32768,
            temperature=inference.get("temperature"),
            top_p=inference.get("top_p"),
            top_k=inference.get("top_k"),
            min_p=inference.get("min_p"),
            repeat_penalty=inference.get("repeat_penalty"),
            reasoning_effort=inference.get("reasoning_effort"),
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
    meta_inference = _inference_for("meta", meta_model)
    configs["meta"] = AgentConfig(
        role=meta_def.role if meta_def else "Meta-Reviewer",
        model=meta_model,
        system_prompt=meta_prompt,
        focus=meta_focus_text,
        max_tokens=16384,
        temperature=meta_inference.get("temperature"),
        top_p=meta_inference.get("top_p"),
        top_k=meta_inference.get("top_k"),
        min_p=meta_inference.get("min_p"),
        repeat_penalty=meta_inference.get("repeat_penalty"),
        reasoning_effort=meta_inference.get("reasoning_effort"),
        presence_penalty=meta_inference.get("presence_penalty"),
        frequency_penalty=meta_inference.get("frequency_penalty"),
    )

    return configs


def build_deliberation_config(
    reviewer_ids: list[str] | None = None,
    max_rounds: int = 2,
) -> DeliberationConfig:
    """Build the standard 3-phase deliberation config for Paper Review.

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
    no_chain_of_thought: bool = False,
) -> IndividualReview:
    """Parse an agent output into a structured IndividualReview."""
    content = apply_output_guardrails(
        output.content,
        no_chain_of_thought=no_chain_of_thought,
    )
    parsed = _extract_json(content)
    model = output.metadata.get("model", "")

    # Build per-reviewer provenance from agent config
    provenance = ReviewerProvenance(
        model_id=model,
        temperature=output.metadata.get("temperature") if agent_config is None else agent_config.temperature,
        top_p=agent_config.top_p if agent_config else None,
        top_k=agent_config.top_k if agent_config else None,
        min_p=agent_config.min_p if agent_config else None,
        repeat_penalty=agent_config.repeat_penalty if agent_config else None,
        reasoning_effort=agent_config.reasoning_effort if agent_config else None,
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
            raw_content=content,
            provenance=provenance,
        )

    return IndividualReview(
        reviewer_role=role,
        agent_id=output.agent_id,
        model=model,
        comments_for_authors=content,
        raw_content=content,
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


def parse_meta_review(output, no_chain_of_thought: bool = False) -> MetaReview:
    """Parse the meta-reviewer output."""
    content = apply_output_guardrails(
        output.content,
        no_chain_of_thought=no_chain_of_thought,
    )
    parsed = _extract_json(content)

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
            raw_content=content,
        )

    return MetaReview(
        author_facing_summary=content,
        raw_content=content,
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
    no_chain_of_thought = prompt_pack_no_chain_of_thought(profile.slug)

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
                    no_chain_of_thought=no_chain_of_thought,
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
                meta = parse_meta_review(
                    phase.outputs[0],
                    no_chain_of_thought=no_chain_of_thought,
                )

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
            "top_k": cfg.top_k,
            "min_p": cfg.min_p,
            "repeat_penalty": cfg.repeat_penalty,
            "reasoning_effort": cfg.reasoning_effort,
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
        pc_chair_review=sanitize_final_review(final_review or {}),
        duration_seconds=result.duration_seconds,
        total_cost=result.total_cost,
        provenance_metadata=provenance,
    )


def session_to_review_packet(session: Any) -> ReviewPacket:
    """Build the canonical ReviewPacket for API and exporter outputs."""
    if not session.result:
        raise ValueError("Session has no result to export")

    result = DeliberationResult.model_validate(session.result)

    conference_slug = session.config.get("metadata", {}).get("conference", "hpdc26")
    try:
        from .conference import load_profile

        profile = load_profile(conference_slug)
    except FileNotFoundError:
        profile = ConferenceProfile(slug=conference_slug, name=conference_slug)

    paper_title = session.config.get("metadata", {}).get("paper_title", "")
    final_review = sanitize_final_review(session.result.get("final_review", {}))

    agent_configs: dict[str, AgentConfig] = {}
    for aid, cfg_dict in session.config.get("agents", {}).items():
        if isinstance(cfg_dict, dict):
            try:
                agent_configs[aid] = AgentConfig.model_validate(cfg_dict)
            except Exception:
                pass

    prompt_pack_version = ""
    try:
        from .prompts import load_prompt_pack

        pack = load_prompt_pack(conference_slug)
        prompt_pack_version = pack.get("version", "")
    except Exception:
        pass

    packet = result_to_packet(
        result,
        profile,
        paper_title,
        final_review=final_review,
        agent_configs=agent_configs,
        prompt_pack_version=prompt_pack_version,
    )

    if session.knowledge_graph:
        try:
            from protoneo.knowledge.graph import KnowledgeGraph

            pg = KnowledgeGraph.model_validate(session.knowledge_graph)
            packet.graph_summary = pg.summary
            packet.graph_node_count = len(pg.nodes)
            packet.graph_edge_count = len(pg.edges)

            if packet.reviews:
                review_dicts = [r.model_dump() for r in packet.reviews]
                packet.graph_utilization = pg.compute_utilization(review_dicts)
        except Exception:
            pass

    parse_provenance = session.app_data.get("parse") if session.app_data else None
    if parse_provenance:
        packet.provenance_metadata["parse"] = parse_provenance

    return packet
