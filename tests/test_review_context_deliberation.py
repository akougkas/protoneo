"""Deliberation context grounding: manuscript + gated graph-fact tiebreaker."""

from apps.paper_review.review_context import (
    ReviewContextMode,
    build_review_context_payload,
)
from protoneo.knowledge.graph import KnowledgeGraph

MANUSCRIPT = (
    "MANUSCRIPT\nSection V reports VisionHPC speedup against cuBLAS on matrix "
    "multiplication, with Figure 3 showing the scaling curve."
)


def _relational_graph() -> KnowledgeGraph:
    g = KnowledgeGraph(paper_title="VisionHPC", section_names=["II", "V"])
    paper = g.add_node("VisionHPC", "Paper", node_id="paper-root")
    method = g.add_node("VisionHPC workflow", "Method", source_section="II")
    baseline = g.add_node("cuBLAS", "Baseline", source_section="V")
    workload = g.add_node("matrix multiplication", "Workload", source_section="V")
    result = g.add_node("VisionHPC outperforms cuBLAS by 2x", "Result", source_section="V")
    claim = g.add_node("VisionHPC achieves 2x speedup", "Claim", source_section="V")
    g.add_edge(paper.id, method.id, "HAS_SECTION")
    g.add_edge(method.id, baseline.id, "COMPARED_AGAINST")
    g.add_edge(method.id, workload.id, "EVALUATES_ON")
    g.add_edge(claim.id, result.id, "SUPPORTED_BY")
    g.update_stats()
    return g


def _index_only_graph() -> KnowledgeGraph:
    g = KnowledgeGraph(paper_title="VisionHPC", section_names=["II"])
    paper = g.add_node("VisionHPC", "Paper", node_id="paper-root")
    method = g.add_node("VisionHPC workflow", "Method", source_section="II")
    g.add_edge(paper.id, method.id, "HAS_SECTION")  # structural only
    g.update_stats()
    return g


def test_production_deliberation_includes_manuscript_and_fact_digest():
    payload = build_review_context_payload(MANUSCRIPT, _relational_graph())
    assert payload.graph_quality["relationship_facts_usable"] is True

    deliberation = payload.render_for_deliberation(
        ReviewContextMode.MARKDOWN_PLUS_STRUCTURED_GRAPH_EVIDENCE
    )
    # Manuscript is available again so reviewers can ground evidence disputes.
    assert "Section V reports VisionHPC speedup" in deliberation
    # Graph-fact tiebreaker digest is present and grounded.
    assert "Graph Relationship Facts (Deliberation Tiebreaker)" in deliberation
    assert "cuBLAS" in deliberation


def test_index_only_graph_withholds_fact_digest_but_keeps_manuscript():
    payload = build_review_context_payload(MANUSCRIPT, _index_only_graph())
    assert payload.graph_quality["relationship_facts_usable"] is False
    assert payload.deliberation_fact_digest == ""

    deliberation = payload.render_for_deliberation(
        ReviewContextMode.MARKDOWN_PLUS_STRUCTURED_GRAPH_EVIDENCE
    )
    assert "Section V reports VisionHPC speedup" in deliberation
    assert "Graph Relationship Facts (Deliberation Tiebreaker)" not in deliberation


def test_graph_only_deliberation_still_withholds_manuscript():
    payload = build_review_context_payload(MANUSCRIPT, _relational_graph())
    deliberation = payload.render_for_deliberation(ReviewContextMode.GRAPH_ONLY)
    assert "Section V reports VisionHPC speedup" not in deliberation
