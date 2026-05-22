"""Phase intent policies for model selection and request shaping."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .types import ModelCapability, ModelInfo, ModelTier


class PhasePolicyLabel(str, Enum):
    FAST_STRUCTURED = "fast_structured"
    VISION_EXTRACT = "vision_extract"
    DEEP_REVIEW = "deep_review"
    META_SYNTHESIS = "meta_synthesis"
    TOOL_USING = "tool_using"


class PhasePolicy(BaseModel):
    label: PhasePolicyLabel
    description: str
    required_capabilities: set[ModelCapability] = Field(default_factory=set)
    preferred_capabilities: set[ModelCapability] = Field(default_factory=set)
    allow_reasoning: bool = False
    prefer_non_reasoning: bool = True
    require_local: bool = False
    prefer_local: bool = False
    prefer_fast: bool = False
    structured_output_required: bool = False
    warning: str = ""
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    enable_thinking: bool | None = None
    reasoning_budget: int | None = None
    thinking_token_budget: int | None = None
    min_reasoning_max_tokens: int = 8192


class PolicyEvaluation(BaseModel):
    hard_ok: bool
    soft_ok: bool
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    score: float = 0.0


PHASE_POLICIES: dict[PhasePolicyLabel, PhasePolicy] = {
    PhasePolicyLabel.FAST_STRUCTURED: PhasePolicy(
        label=PhasePolicyLabel.FAST_STRUCTURED,
        description="Fast deterministic JSON extraction without reasoning output.",
        preferred_capabilities={ModelCapability.STRUCTURED_OUTPUT},
        allow_reasoning=False,
        prefer_non_reasoning=True,
        require_local=True,
        prefer_fast=True,
        structured_output_required=True,
        temperature=0.2,
        top_k=1,
        enable_thinking=False,
        warning="Reasoning models are discouraged for graph extraction phases.",
    ),
    PhasePolicyLabel.VISION_EXTRACT: PhasePolicy(
        label=PhasePolicyLabel.VISION_EXTRACT,
        description="Concise visual extraction for figures/tables.",
        required_capabilities={ModelCapability.VISION},
        preferred_capabilities={ModelCapability.STRUCTURED_OUTPUT},
        allow_reasoning=False,
        prefer_non_reasoning=True,
        prefer_fast=True,
        temperature=0.2,
        top_k=1,
        enable_thinking=False,
    ),
    PhasePolicyLabel.DEEP_REVIEW: PhasePolicy(
        label=PhasePolicyLabel.DEEP_REVIEW,
        description="Reviewer judgment where stronger reasoning can be useful.",
        preferred_capabilities={ModelCapability.STRUCTURED_OUTPUT, ModelCapability.EXTENDED_THINKING},
        allow_reasoning=True,
        prefer_non_reasoning=False,
        prefer_local=False,
        temperature=0.6,
        top_p=0.95,
        enable_thinking=True,
        reasoning_budget=16384,
        thinking_token_budget=17408,
        min_reasoning_max_tokens=20480,
    ),
    PhasePolicyLabel.META_SYNTHESIS: PhasePolicy(
        label=PhasePolicyLabel.META_SYNTHESIS,
        description="Final synthesis with reasoning-capable models allowed and sanitized output.",
        preferred_capabilities={ModelCapability.STRUCTURED_OUTPUT, ModelCapability.EXTENDED_THINKING},
        allow_reasoning=True,
        prefer_non_reasoning=False,
        temperature=0.6,
        top_p=0.95,
        enable_thinking=True,
        reasoning_budget=8192,
        thinking_token_budget=9216,
        min_reasoning_max_tokens=12288,
    ),
    PhasePolicyLabel.TOOL_USING: PhasePolicy(
        label=PhasePolicyLabel.TOOL_USING,
        description="Tool-calling phase; model/provider must support tool use.",
        required_capabilities={ModelCapability.FUNCTION_CALLING},
        preferred_capabilities={ModelCapability.STREAMING},
        allow_reasoning=True,
        prefer_non_reasoning=False,
    ),
}


_GRAPH_PHASES = {"ontology", "extraction", "coref", "verification", "graph"}
_META_PHASES = {"meta", "meta_reviewer", "pc_chair", "final_synthesis"}


def policy_for_label(label: str | PhasePolicyLabel | None) -> PhasePolicy | None:
    if not label:
        return None
    try:
        return PHASE_POLICIES[PhasePolicyLabel(str(label))]
    except ValueError:
        return None


def policy_for_phase(phase_key: str, *, tool_using: bool = False) -> PhasePolicy:
    if tool_using:
        return PHASE_POLICIES[PhasePolicyLabel.TOOL_USING]
    key = phase_key.lower()
    if key in _GRAPH_PHASES:
        return PHASE_POLICIES[PhasePolicyLabel.FAST_STRUCTURED]
    if key in _META_PHASES:
        return PHASE_POLICIES[PhasePolicyLabel.META_SYNTHESIS]
    return PHASE_POLICIES[PhasePolicyLabel.DEEP_REVIEW]


def evaluate_model_for_policy(
    model: ModelInfo,
    policy: PhasePolicy,
    *,
    require_local: bool | None = None,
) -> PolicyEvaluation:
    """Evaluate a model against hard requirements and soft intent."""
    reasons: list[str] = []
    warnings: list[str] = []
    score = 0.0

    required = set(policy.required_capabilities)
    missing = sorted(cap.value for cap in required if cap not in model.capabilities)
    if missing:
        reasons.append(f"missing capabilities: {', '.join(missing)}")

    local_required = policy.require_local if require_local is None else require_local
    if local_required and model.tier != ModelTier.LOCAL:
        reasons.append("requires local/LAN model")

    hard_ok = not reasons
    soft_ok = True

    reasoning = ModelCapability.EXTENDED_THINKING in model.capabilities
    if reasoning and not policy.allow_reasoning:
        soft_ok = False
        warnings.append("reasoning/thinking model selected for non-reasoning phase")
        score -= 100
    elif reasoning and policy.allow_reasoning:
        score += 20
    elif policy.prefer_non_reasoning:
        score += 30

    for cap in policy.preferred_capabilities:
        if cap in model.capabilities:
            score += 12

    if policy.prefer_local and model.tier == ModelTier.LOCAL:
        score += 8

    if policy.prefer_fast:
        if model.speed_tps:
            score += min(model.speed_tps, 200) / 10
        elif not reasoning:
            score += 5

    if model.max_context:
        score += min(model.max_context, 256_000) / 256_000

    return PolicyEvaluation(
        hard_ok=hard_ok,
        soft_ok=soft_ok,
        reasons=reasons,
        warnings=warnings,
        score=score,
    )


def phase_policy_metadata() -> dict[str, Any]:
    return {
        label.value: {
            **policy.model_dump(mode="json"),
            "required_capabilities": sorted(c.value for c in policy.required_capabilities),
            "preferred_capabilities": sorted(c.value for c in policy.preferred_capabilities),
        }
        for label, policy in PHASE_POLICIES.items()
    }
