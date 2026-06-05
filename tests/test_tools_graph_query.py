"""Tests for the kernel graph-query tool, registry dispatch, and agent wiring."""

import pytest

from protoneo.knowledge.graph import KnowledgeGraph
from protoneo.tools import (
    GraphQueryTool,
    create_tool_registry,
    graph_fact_digest,
    query_graph,
)


def _graph() -> KnowledgeGraph:
    g = KnowledgeGraph(paper_title="VisionHPC", section_names=["II", "V"])
    paper = g.add_node("VisionHPC", "Paper", node_id="paper-root")
    method = g.add_node("VisionHPC workflow", "Method", source_section="II")
    baseline = g.add_node("cuBLAS", "Baseline", source_section="V")
    workload = g.add_node("matrix multiplication", "Workload", source_section="V")
    metric = g.add_node("speedup", "Metric", source_section="V")
    result = g.add_node("VisionHPC outperforms cuBLAS by 2x", "Result", source_section="V")
    supported = g.add_node("VisionHPC achieves 2x speedup", "Claim", source_section="V")
    dangling = g.add_node("VisionHPC generalizes to all kernels", "Claim", source_section="VI")
    fig = g.add_node(
        "Figure 3: GPU speedup",
        "Figure",
        source_section="V",
        attributes={"description": "GPU speedup curves vs cuBLAS.", "page": 5},
    )
    g.add_edge(paper.id, method.id, "HAS_SECTION")
    g.add_edge(method.id, baseline.id, "COMPARED_AGAINST")
    g.add_edge(method.id, workload.id, "EVALUATES_ON")
    g.add_edge(result.id, metric.id, "USES")
    g.add_edge(supported.id, result.id, "SUPPORTED_BY")
    g.add_edge(supported.id, fig.id, "APPEARS_IN")
    g.update_stats()
    return g


def test_overview_reports_counts():
    data = query_graph(_graph(), "overview")
    assert data["available"] is True
    by_type = data["results"]["semantic_node_count_by_type"]
    assert by_type.get("Claim") == 2
    assert by_type.get("Method") == 1
    assert data["results"]["semantic_edge_count"] >= 4


def test_claims_without_support_flags_only_dangling_claim():
    data = query_graph(_graph(), "claims_without_support")
    flagged = [r["claim"] for r in data["results"]]
    assert any("generalizes to all kernels" in c for c in flagged)
    assert not any("achieves 2x speedup" in c for c in flagged)
    assert data["count"] == 1


def test_methods_evaluation_links_method_to_baseline_and_workload():
    data = query_graph(_graph(), "methods_evaluation")
    row = data["results"][0]
    assert row["has_evaluation"] is True
    links = row["evaluation_links"]
    assert "cuBLAS" in links.get("Baseline", [])
    assert "matrix multiplication" in links.get("Workload", [])


def test_baselines_reports_comparison_edges():
    data = query_graph(_graph(), "baselines")
    assert data["count"] == 1
    assert "VisionHPC workflow" in data["results"][0]["compared_against"]


def test_claim_evidence_connects_supported_claim_to_figure_and_result():
    data = query_graph(_graph(), "claim_evidence")
    supported = next(r for r in data["results"] if "achieves 2x" in r["claim"])
    ev_types = {e["type"] for e in supported["evidence"]}
    assert {"Result", "Figure"} <= ev_types


def test_section_coverage_counts_entities_per_section():
    data = query_graph(_graph(), "section_coverage")
    sections = {r["section"]: r["entity_count"] for r in data["results"]}
    assert sections.get("V", 0) >= 4


def test_entity_lookup_returns_neighbors():
    data = query_graph(_graph(), "entity", target="cuBLAS")
    assert data["count"] == 1
    node = data["results"][0]
    assert node["type"] == "Baseline"
    assert any(n["type"] == "Method" for n in node["neighbors"])


def test_unknown_query_type_is_graceful():
    data = query_graph(_graph(), "does_not_exist")
    assert data["error"] == "unknown_query_type"
    assert "overview" in data["supported_query_types"]


def test_empty_graph_is_unavailable():
    data = query_graph(KnowledgeGraph(), "overview")
    assert data["available"] is False
    assert data["count"] == 0


def test_graph_fact_digest_is_deterministic_and_grounded():
    g = _graph()
    digest_a = graph_fact_digest(g)
    digest_b = graph_fact_digest(g)
    assert digest_a == digest_b
    assert "cuBLAS" in digest_a
    assert "compared against" in digest_a.lower()
    # No raw internal edge-count leakage in the digest.
    assert "edge" not in digest_a.lower()


def test_registry_binds_and_dispatches_query_graph_tool():
    g = _graph()
    registry = create_tool_registry(graph=g)
    names = {t["name"] for t in registry.available_tools()}
    assert "query_graph" in names


def test_unbound_graph_tool_is_unavailable():
    registry = create_tool_registry()  # no graph
    names = {t["name"] for t in registry.available_tools()}
    assert "query_graph" not in names
    assert GraphQueryTool().available() is False


@pytest.mark.asyncio
async def test_registry_dispatch_executes_query_graph():
    g = _graph()
    registry = create_tool_registry(graph=g)
    result = await registry.dispatch("query_graph", query_type="baselines")
    assert result.source == "knowledge_graph"
    assert result.data["count"] == 1


@pytest.mark.asyncio
async def test_registry_dispatch_unknown_tool_raises():
    registry = create_tool_registry()
    with pytest.raises(KeyError):
        await registry.dispatch("nonexistent_tool")


@pytest.mark.asyncio
async def test_base_agent_call_tool_dispatches_through_registry():
    from protoneo.agents.base import BaseAgent

    g = _graph()
    registry = create_tool_registry(graph=g)
    agent = BaseAgent(
        role="Technical Reviewer",
        model="test/model",
        system_prompt="",
        llm_client=None,  # not used for tool calls
        tools=registry,
    )
    assert any(t["name"] == "query_graph" for t in agent.available_tools())
    result = await agent.call_tool("query_graph", query_type="claims_without_support")
    assert result.data["query_type"] == "claims_without_support"


@pytest.mark.asyncio
async def test_base_agent_without_tools_rejects_tool_call():
    from protoneo.agents.base import BaseAgent

    agent = BaseAgent(role="r", model="m", system_prompt="", llm_client=None)
    assert agent.available_tools() == []
    with pytest.raises(RuntimeError):
        await agent.call_tool("query_graph")
