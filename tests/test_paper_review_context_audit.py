from protoneo.config.schema import AgentConfig, DeliberationConfig, PhaseConfig
from protoneo.knowledge.graph import KnowledgeGraph

from apps.paper_review.context_audit import build_context_audit_artifact
from apps.paper_review.graph_usage import compute_review_graph_value_metrics
from apps.paper_review.review_context import (
    ReviewContextMode,
    build_review_context_payload,
)


def _graph() -> KnowledgeGraph:
    graph = KnowledgeGraph(paper_title="VisionHPC")
    root = graph.add_node("VisionHPC", "Paper", node_id="paper-root")
    method = graph.add_node("VisionHPC workflow", "Method", source_section="II")
    baseline = graph.add_node("cuBLAS", "Baseline", source_section="V")
    workload = graph.add_node("matrix multiplication", "Workload", source_section="V")
    metric = graph.add_node("speedup", "Metric", source_section="V")
    result = graph.add_node("VisionHPC outperforms cuBLAS", "Result", source_section="V")
    fig = graph.add_node(
        "Figure 3: GPU speedup",
        "Figure",
        source_section="V",
        attributes={"description": "GPU speedup curves against cuBLAS.", "page": 5},
    )
    graph.add_edge(root.id, method.id, "HAS_SECTION")
    graph.add_edge(method.id, baseline.id, "COMPARED_AGAINST")
    graph.add_edge(method.id, workload.id, "EVALUATES_ON")
    graph.add_edge(result.id, metric.id, "USES")
    graph.add_edge(result.id, fig.id, "APPEARS_IN")
    graph.summary = graph.to_agent_briefing()
    graph.update_stats()
    return graph


def test_context_modes_render_distinct_packets():
    payload = build_review_context_payload(
        "MANUSCRIPT\nSection V reports VisionHPC speedup against cuBLAS.",
        _graph(),
    )

    markdown_only = payload.render_for_independent_review(ReviewContextMode.MARKDOWN_ONLY)
    graph_only = payload.render_for_independent_review(ReviewContextMode.GRAPH_ONLY)
    required = payload.render_for_independent_review(
        ReviewContextMode.MARKDOWN_PLUS_REQUIRED_GRAPH_CITATIONS
    )
    full = payload.render_full_deliberation_context(
        independent_reviews=["Technical review cites Figure 3."],
        deliberation_turns=["Skeptic asks whether cuBLAS was tuned."],
    )

    assert "MANUSCRIPT" in markdown_only
    assert "MANUSCRIPT" not in graph_only
    assert "Evidence Citation Requirement" in required
    assert "Independent Review Transcript" in full
    assert "Deliberation Transcript" in full


def test_context_audit_artifact_tracks_all_modes_and_agent_prompts():
    payload = build_review_context_payload("MANUSCRIPT\nVisionHPC text.", _graph())
    agents = {
        "technical": AgentConfig(
            role="Technical Reviewer",
            model="lan-mini/gemma",
            system_prompt="Review technically.",
            phase_policy="deep_review",
        )
    }
    delib = DeliberationConfig(
        pattern="independent_synthesis",
        phases=[
            PhaseConfig(name="independent_review", mode="parallel", agents=["technical"]),
            PhaseConfig(name="meta_review", mode="sequential", agents=["technical"]),
        ],
    )

    audit = build_context_audit_artifact(
        context_payload=payload,
        agent_configs=agents,
        deliberation_config=delib,
        active_mode=ReviewContextMode.MARKDOWN_PLUS_STRUCTURED_GRAPH_EVIDENCE,
        include_prompt_text=False,
    )

    assert audit["model_calls_performed"] is False
    assert set(audit["packets"]) == {mode.value for mode in ReviewContextMode}
    assert audit["packets"]["full_deliberation_context"]["independent_review_user_prompt"]["chars"] > 0
    assert audit["agent_system_prompts"]["technical"]["system_prompt"]["approx_tokens"] > 0


def test_graph_value_metrics_are_deterministic_and_conservative():
    reviews = [
        {
            "agent_id": "technical",
            "summary": "VisionHPC compares generated kernels against cuBLAS.",
            "strengths": [
                {"point": "The paper includes baseline comparison.", "evidence": "Section V and Figure 3."}
            ],
            "weaknesses": [
                {"point": "The review needs more tuning detail."}
            ],
            "citations": [
                {"claim": "cuBLAS comparison", "section": "V", "graph_ref": "cuBLAS"}
            ],
        }
    ]

    metrics = compute_review_graph_value_metrics(
        _graph(),
        reviews,
        prompt_token_estimates={"independent_review": 1000},
    )

    assert metrics["explicit_entity_utilization"]["utilization_ratio"] > 0
    assert metrics["figure_table_equation_coverage"]["total"] == 1
    assert metrics["evidence_citation_precision_recall"]["precision"] == 1.0
    assert metrics["unsupported_claim_rate"]["unsupported_count"] == 1
    assert metrics["token_cost_per_useful_grounded_fact"]["prompt_tokens"] == 1000
