"""Offline context rendering and audit artifacts for paper review.

This module is app-owned and deliberately does not call models. It renders the
same manuscript/graph context variants that live review uses so reviewers,
ablation runs, and session metadata can be inspected before launching any LLM.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from protoneo.agents.types import Document
from protoneo.config.schema import AgentConfig, DeliberationConfig
from protoneo.knowledge.graph import KnowledgeGraph

from .conference import ConferenceProfile, load_profile
from .review import build_agent_configs, build_deliberation_config, build_user_message
from .review_context import (
    ReviewContextMode,
    ReviewContextPayload,
    build_review_context_payload,
)


def approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def prompt_metric(text: str, *, include_text: bool = False) -> dict[str, Any]:
    metric: dict[str, Any] = {
        "chars": len(text),
        "approx_tokens": approx_tokens(text),
    }
    if include_text:
        metric["text"] = text
    return metric


def build_context_audit_artifact(
    *,
    context_payload: ReviewContextPayload,
    agent_configs: dict[str, AgentConfig],
    deliberation_config: DeliberationConfig | None = None,
    active_mode: str | ReviewContextMode | None = None,
    independent_reviews: list[str] | None = None,
    deliberation_turns: list[str] | None = None,
    include_prompt_text: bool = True,
) -> dict[str, Any]:
    """Render review context packets and prompt metrics without model calls."""
    active = ReviewContextMode.coerce(active_mode)
    independent_reviews = independent_reviews or []
    deliberation_turns = deliberation_turns or []

    packets: dict[str, Any] = {}
    for mode in ReviewContextMode:
        independent_prompt = context_payload.render_for_independent_review(mode)
        deliberation_context = context_payload.render_for_deliberation(mode)
        full_context = context_payload.render_full_deliberation_context(
            independent_reviews=independent_reviews,
            deliberation_turns=deliberation_turns,
            mode=mode,
        )
        packets[mode.value] = {
            "independent_review_user_prompt": prompt_metric(
                independent_prompt,
                include_text=include_prompt_text,
            ),
            "deliberation_context": prompt_metric(
                deliberation_context,
                include_text=include_prompt_text,
            ),
            "full_deliberation_context": prompt_metric(
                full_context,
                include_text=include_prompt_text,
            ),
        }

    agents: dict[str, Any] = {}
    for agent_id, cfg in agent_configs.items():
        agents[agent_id] = {
            "role": cfg.role,
            "model": cfg.model,
            "phase_policy": cfg.phase_policy,
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
            "top_k": cfg.top_k,
            "min_p": cfg.min_p,
            "repeat_penalty": cfg.repeat_penalty,
            "reasoning_effort": cfg.reasoning_effort,
            "system_prompt": prompt_metric(
                cfg.system_prompt,
                include_text=include_prompt_text,
            ),
        }

    deliberation = deliberation_config.model_dump(mode="json") if deliberation_config else {}
    return {
        "kind": "paper_review_context_audit",
        "version": "1.0",
        "active_mode": active.value,
        "live_execution_required": True,
        "model_calls_performed": False,
        "component_audit": context_payload.audit(
            active,
            independent_reviews=independent_reviews,
            deliberation_turns=deliberation_turns,
        ),
        "packets": packets,
        "agent_system_prompts": agents,
        "deliberation_config": deliberation,
    }


def render_offline_context_audit(
    *,
    markdown: str,
    graph: KnowledgeGraph,
    profile: ConferenceProfile,
    agent_configs: dict[str, AgentConfig],
    deliberation_config: DeliberationConfig | None = None,
    filename: str = "paper.md",
    active_mode: str | ReviewContextMode | None = None,
    include_prompt_text: bool = True,
) -> dict[str, Any]:
    """Build a full offline context audit from markdown and an imported graph."""
    doc = Document(
        document_id="offline-context-audit",
        filename=filename,
        text=markdown,
        markdown=markdown,
    )
    user_message = build_user_message(doc, profile)
    context_payload = build_review_context_payload(user_message, graph)
    return build_context_audit_artifact(
        context_payload=context_payload,
        agent_configs=agent_configs,
        deliberation_config=deliberation_config,
        active_mode=active_mode,
        include_prompt_text=include_prompt_text,
    )


def summarize_context_audit(audit: dict[str, Any]) -> dict[str, Any]:
    """Return a compact summary suitable for terminal output and API responses."""
    component_audit = audit.get("component_audit", {})
    packets = audit.get("packets", {})
    return {
        "kind": audit.get("kind"),
        "version": audit.get("version"),
        "active_mode": audit.get("active_mode"),
        "model_calls_performed": audit.get("model_calls_performed", False),
        "live_execution_required": audit.get("live_execution_required", True),
        "components": component_audit.get("components", []),
        "rendered": component_audit.get("rendered", {}),
        "mode_metrics": component_audit.get("mode_metrics", {}),
        "graph_quality": component_audit.get("graph_quality", {}),
        "graph_metrics": component_audit.get("graph_metrics", {}),
        "agent_system_prompts": {
            agent_id: {
                "role": data.get("role", ""),
                "model": data.get("model", ""),
                "phase_policy": data.get("phase_policy", ""),
                "system_prompt": {
                    "chars": data.get("system_prompt", {}).get("chars", 0),
                    "approx_tokens": data.get("system_prompt", {}).get("approx_tokens", 0),
                },
            }
            for agent_id, data in audit.get("agent_system_prompts", {}).items()
        },
        "packet_prompt_metrics": {
            mode: {
                key: {
                    "chars": value.get("chars", 0),
                    "approx_tokens": value.get("approx_tokens", 0),
                }
                for key, value in packet.items()
            }
            for mode, packet in packets.items()
        },
    }


def load_graph_file(path: Path) -> KnowledgeGraph:
    data = json.loads(path.read_text())
    graph_data = data.get("graph", data) if isinstance(data, dict) else data
    graph = KnowledgeGraph.model_validate(graph_data)
    graph.update_stats()
    if not graph.summary:
        graph.summary = graph.to_agent_briefing()
    return graph


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render paper-review context packets and metrics without model calls.",
    )
    parser.add_argument("--markdown", required=True, type=Path)
    parser.add_argument("--graph", required=True, type=Path)
    parser.add_argument("--conference", default="adaptive")
    parser.add_argument(
        "--context-mode",
        default=ReviewContextMode.MARKDOWN_PLUS_STRUCTURED_GRAPH_EVIDENCE.value,
        choices=[mode.value for mode in ReviewContextMode],
    )
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Do not include full prompt text in the output artifact.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    profile = load_profile(args.conference)
    markdown = args.markdown.read_text(errors="ignore")
    graph = load_graph_file(args.graph)
    agent_configs = build_agent_configs(profile, args.conference)
    delib_config = build_deliberation_config(
        reviewer_ids=[key for key in agent_configs if key != "meta"],
        max_rounds=args.max_rounds,
    )
    audit = render_offline_context_audit(
        markdown=markdown,
        graph=graph,
        profile=profile,
        agent_configs=agent_configs,
        deliberation_config=delib_config,
        filename=args.markdown.name,
        active_mode=args.context_mode,
        include_prompt_text=not args.summary_only,
    )
    payload = summarize_context_audit(audit) if args.summary_only or not args.output else audit
    rendered = json.dumps(payload, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
        print(json.dumps(summarize_context_audit(audit), indent=2))
        print(f"\nWrote context audit artifact: {args.output}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
