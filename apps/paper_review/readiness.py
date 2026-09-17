"""Resolve and validate a review plan before parsing or starting model calls."""

from typing import Any

from protoneo.config.schema import AgentConfig, ProtoNeoConfig
from protoneo.llm.model_catalog import build_model_catalog
from protoneo.llm.providers.registry import get_provider_registry
from protoneo.llm.registry import CapabilityRegistry
from protoneo.llm.settings import endpoint_map, load_settings, provider_is_enabled

from .review import resolve_paper_review_model, _load_settings_context

GRAPH_STEPS = ("ontology", "extraction", "coref", "verification")


def resolve_graph_models(model_map: dict[str, Any]) -> dict[str, str]:
    settings, preset = _load_settings_context()
    context = (settings, preset, CapabilityRegistry.from_settings(settings))
    return {step: resolve_paper_review_model(
        step, model_map, fallback_keys=("ontology", "graph"),
        require_local=True, phase_policy="fast_structured", routing_context=context,
    ) for step in GRAPH_STEPS}


def review_readiness(
    agents: dict[str, AgentConfig], model_map: dict[str, Any], *,
    skip_graph: bool = False, graph_only: bool = False, max_rounds: int = 2,
    config: ProtoNeoConfig | None = None,
) -> dict[str, Any]:
    settings = load_settings()
    registry = CapabilityRegistry.from_settings(settings)
    endpoints = endpoint_map(settings)
    credentials = get_provider_registry()
    catalog = {m["provider_model_id"]: m for m in build_model_catalog(settings, registry)}
    graph_models = {} if skip_graph else resolve_graph_models(model_map)
    assignments = dict(graph_models)
    if not graph_only:
        assignments.update({key: agent.model for key, agent in agents.items()})
    blockers, warnings, rows = [], [], []
    providers = (config or ProtoNeoConfig.from_env()).providers
    for role, model in assignments.items():
        if not model:
            blockers.append(f"Choose a model for {role.replace('_', ' ')} in the panel or Settings.")
            continue
        info = registry.get(model)
        provider = info.provider
        entry = catalog.get(model, {})
        if not provider_is_enabled(provider, settings):
            blockers.append(f"{role}: provider '{provider}' is disabled. Enable it in Settings.")
        elif entry.get("availability") == "unsupported" or entry.get("review_routable") is False:
            blockers.append(f"{role}: '{model}' is not available for review requests.")
        elif provider not in endpoints:
            configured = providers.get(provider)
            if not ((configured and (configured.api_key or configured.base_url))
                    or credentials.resolve_api_key(provider)):
                blockers.append(f"{role}: connect '{provider}' in Settings or configure its API key.")
        rows.append({"role": role, "model": model,
                     "location": info.runtime_location, "context_length": info.max_context})
    reviewers = [agent for key, agent in agents.items() if key != "meta"]
    if not graph_only and len({a.model for a in reviewers}) == 1 and len(reviewers) > 1:
        warnings.append("Reviewers share one model. Different roles provide distinct perspectives, but their errors may be correlated.")
    if skip_graph and not graph_only:
        warnings.append("Reviewers will use the parsed manuscript without a knowledge graph.")
    return {
        "ready": not blockers, "blockers": blockers, "warnings": warnings,
        "assignments": rows, "graph_models": graph_models,
        "reviewer_count": 0 if graph_only else len(reviewers),
        "review_turns": 0 if graph_only else len(reviewers) * (1 + max_rounds) + 1,
        "discussion_rounds": max_rounds,
        "availability_checked": False,
    }
