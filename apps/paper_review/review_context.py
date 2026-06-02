"""Paper-review source context assembly.

This module is application-owned. The ProtoNeo kernel can store documents and
knowledge graphs, but paper-review decides how manuscript markdown, graph
briefings, visual evidence, and ablation variants are rendered for reviewers.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any

from protoneo.knowledge.graph import GraphNode, KnowledgeGraph


STRUCTURAL_NODE_TYPES = {"Paper", "Section", "Diagram", "Figure", "Table", "Reference", "Equation"}
STRUCTURAL_EDGE_TYPES = {"HAS_SECTION", "CONTAINS", "HAS_ARTIFACT", "APPEARS_IN"}
MIN_REVIEW_SEMANTIC_EDGES = 3


class ReviewContextMode(str, Enum):
    """Offline/live context variants for graph-value ablations."""

    MARKDOWN_ONLY = "markdown_only"
    GRAPH_ONLY = "graph_only"
    MARKDOWN_PLUS_GRAPH_BRIEFING = "markdown_plus_graph_briefing"
    MARKDOWN_PLUS_STRUCTURED_GRAPH_EVIDENCE = "markdown_plus_structured_graph_evidence"
    MARKDOWN_PLUS_REQUIRED_GRAPH_CITATIONS = "markdown_plus_required_graph_citations"
    FULL_DELIBERATION_CONTEXT = "full_deliberation_context"

    @classmethod
    def coerce(cls, value: str | "ReviewContextMode" | None) -> "ReviewContextMode":
        if isinstance(value, cls):
            return value
        if value:
            try:
                return cls(str(value))
            except ValueError:
                pass
        return cls.MARKDOWN_PLUS_STRUCTURED_GRAPH_EVIDENCE


@dataclass(frozen=True)
class ContextComponent:
    name: str
    text: str
    source: str

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def approx_tokens(self) -> int:
        return _approx_tokens(self.text)

    def metric(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "chars": self.char_count,
            "approx_tokens": self.approx_tokens,
            "included": bool(self.text),
        }


@dataclass(frozen=True)
class ReviewContextPayload:
    manuscript: str
    graph_policy: str
    graph_briefing: str
    structured_graph_analysis: str
    visual_evidence_ledger: str
    required_citation_instructions: str
    graph_quality: dict[str, Any]
    graph_metrics: dict[str, Any]

    def render_for_independent_review(
        self,
        mode: str | ReviewContextMode | None = None,
    ) -> str:
        mode = ReviewContextMode.coerce(mode)
        if mode == ReviewContextMode.MARKDOWN_ONLY:
            return self.manuscript
        if mode == ReviewContextMode.GRAPH_ONLY:
            return _join_parts([
                _graph_only_notice(),
                self.graph_policy,
                self.graph_briefing,
                self.structured_graph_analysis,
                self.visual_evidence_ledger,
            ])
        if mode == ReviewContextMode.MARKDOWN_PLUS_GRAPH_BRIEFING:
            return _join_parts([self.manuscript, self.graph_policy, self.graph_briefing])
        if mode == ReviewContextMode.MARKDOWN_PLUS_REQUIRED_GRAPH_CITATIONS:
            return _join_parts([
                self.manuscript,
                self.graph_policy,
                self.graph_briefing,
                self.structured_graph_analysis,
                self.visual_evidence_ledger,
                self.required_citation_instructions,
            ])
        if mode == ReviewContextMode.FULL_DELIBERATION_CONTEXT:
            return _join_parts([
                self.manuscript,
                self.graph_policy,
                self.graph_briefing,
                self.structured_graph_analysis,
                self.visual_evidence_ledger,
                self.required_citation_instructions,
            ])
        return _join_parts([
            self.manuscript,
            self.graph_policy,
            self.graph_briefing,
            self.structured_graph_analysis,
            self.visual_evidence_ledger,
        ])

    def render_for_deliberation(
        self,
        mode: str | ReviewContextMode | None = None,
    ) -> str:
        mode = ReviewContextMode.coerce(mode)
        if mode == ReviewContextMode.MARKDOWN_ONLY:
            return ""
        if mode == ReviewContextMode.MARKDOWN_PLUS_GRAPH_BRIEFING:
            return _join_parts([self.graph_policy, self.graph_briefing])
        if mode == ReviewContextMode.MARKDOWN_PLUS_REQUIRED_GRAPH_CITATIONS:
            return _join_parts([
                self.graph_policy,
                self.graph_briefing,
                self.structured_graph_analysis,
                self.visual_evidence_ledger,
                self.required_citation_instructions,
            ])
        if mode == ReviewContextMode.FULL_DELIBERATION_CONTEXT:
            return _join_parts([
                self.manuscript,
                self.graph_policy,
                self.graph_briefing,
                self.structured_graph_analysis,
                self.visual_evidence_ledger,
                self.required_citation_instructions,
            ])
        return _join_parts([
            self.graph_policy,
            self.graph_briefing,
            self.structured_graph_analysis,
            self.visual_evidence_ledger,
        ])

    def render_full_deliberation_context(
        self,
        *,
        independent_reviews: list[str] | None = None,
        deliberation_turns: list[str] | None = None,
        mode: str | ReviewContextMode | None = None,
    ) -> str:
        """Render the complete source + transcript context for synthesis audits."""
        source = self.render_for_deliberation(
            mode or ReviewContextMode.FULL_DELIBERATION_CONTEXT
        )
        reviews = _render_transcript_section("Independent Review Transcript", independent_reviews or [])
        deliberation = _render_transcript_section("Deliberation Transcript", deliberation_turns or [])
        return _join_parts([source, reviews, deliberation])

    def audit(
        self,
        active_mode: str | ReviewContextMode | None = None,
        *,
        independent_reviews: list[str] | None = None,
        deliberation_turns: list[str] | None = None,
    ) -> dict[str, Any]:
        mode = ReviewContextMode.coerce(active_mode)
        independent = self.render_for_independent_review(mode)
        deliberation = self.render_for_deliberation(mode)
        full_deliberation = self.render_full_deliberation_context(
            independent_reviews=independent_reviews,
            deliberation_turns=deliberation_turns,
            mode=ReviewContextMode.FULL_DELIBERATION_CONTEXT,
        )
        components = [
            ContextComponent("manuscript_markdown", self.manuscript, "document_markdown_or_text"),
            ContextComponent("graph_policy", self.graph_policy, "paper_review.review_context"),
            ContextComponent("graph_briefing", self.graph_briefing, "KnowledgeGraph.summary_or_to_agent_briefing"),
            ContextComponent("structured_graph_analysis", self.structured_graph_analysis, "paper_review.review_context"),
            ContextComponent("visual_evidence_ledger", self.visual_evidence_ledger, "KnowledgeGraph Figure/Table/Equation nodes"),
            ContextComponent("required_citation_instructions", self.required_citation_instructions, "paper_review.review_context"),
            ContextComponent(
                "review_transcript",
                _render_transcript_section("Independent Review Transcript", independent_reviews or []),
                "deliberation independent-review outputs",
            ),
            ContextComponent(
                "deliberation_transcript",
                _render_transcript_section("Deliberation Transcript", deliberation_turns or []),
                "deliberation round outputs",
            ),
        ]
        return {
            "active_mode": mode.value,
            "components": [component.metric() for component in components],
            "rendered": {
                "independent_review": {
                    "chars": len(independent),
                    "approx_tokens": _approx_tokens(independent),
                },
                "deliberation": {
                    "chars": len(deliberation),
                    "approx_tokens": _approx_tokens(deliberation),
                },
                "full_deliberation_context": {
                    "chars": len(full_deliberation),
                    "approx_tokens": _approx_tokens(full_deliberation),
                },
            },
            "mode_metrics": _mode_metrics(self, independent_reviews or [], deliberation_turns or []),
            "graph_quality": self.graph_quality,
            "graph_metrics": self.graph_metrics,
        }


def review_graph_quality(graph: KnowledgeGraph) -> dict[str, Any]:
    """Classify whether graph relationships are safe reviewer evidence."""
    semantic_nodes = _semantic_nodes(graph)
    semantic_edges = _semantic_edges(graph)
    grounding = graph.grounding_summary()
    relationship_facts_usable = len(semantic_edges) >= MIN_REVIEW_SEMANTIC_EDGES
    if not graph.nodes:
        mode = "unavailable"
    elif relationship_facts_usable:
        mode = "relational"
    else:
        mode = "index_only"
    return {
        "mode": mode,
        "relationship_facts_usable": relationship_facts_usable,
        "semantic_node_count": len(semantic_nodes),
        "semantic_edge_count": len(semantic_edges),
        "threshold": MIN_REVIEW_SEMANTIC_EDGES,
        **grounding,
    }


def build_review_context_payload(
    manuscript_message: str,
    graph: KnowledgeGraph,
) -> ReviewContextPayload:
    """Build all reviewer-facing paper-review context sections and metrics."""
    quality = review_graph_quality(graph)
    briefing = graph.summary or graph.to_agent_briefing()
    if not briefing and quality["mode"] == "unavailable":
        briefing = "No reviewer-facing graph summary is available for this session."
    graph_metrics = _graph_metrics(graph, quality)
    return ReviewContextPayload(
        manuscript=manuscript_message,
        graph_policy=_build_graph_policy_context(quality),
        graph_briefing=_wrap_section("Knowledge Graph Briefing", briefing),
        structured_graph_analysis=_build_review_graph_analysis(graph, quality),
        visual_evidence_ledger=_build_visual_evidence_ledger(graph),
        required_citation_instructions=_required_citation_instructions(),
        graph_quality=quality,
        graph_metrics=graph_metrics,
    )


def _semantic_nodes(graph: KnowledgeGraph) -> list[GraphNode]:
    return [n for n in graph.nodes if n.node_type not in STRUCTURAL_NODE_TYPES]


def _semantic_edges(graph: KnowledgeGraph) -> list[Any]:
    return [e for e in graph.edges if e.edge_type not in STRUCTURAL_EDGE_TYPES]


def _approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _join_parts(parts: list[str]) -> str:
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


def _wrap_section(title: str, body: str) -> str:
    if not body:
        return ""
    return f"## {title}\n\n{body.strip()}\n"


def _render_transcript_section(title: str, entries: list[str]) -> str:
    cleaned = [str(entry).strip() for entry in entries if str(entry).strip()]
    if not cleaned:
        return ""
    return _wrap_section(title, "\n\n---\n\n".join(cleaned))


def _mode_metrics(
    payload: ReviewContextPayload,
    independent_reviews: list[str],
    deliberation_turns: list[str],
) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for mode in ReviewContextMode:
        independent = payload.render_for_independent_review(mode)
        deliberation = payload.render_for_deliberation(mode)
        full = payload.render_full_deliberation_context(
            independent_reviews=independent_reviews,
            deliberation_turns=deliberation_turns,
            mode=mode,
        )
        metrics[mode.value] = {
            "independent_review": {
                "chars": len(independent),
                "approx_tokens": _approx_tokens(independent),
            },
            "deliberation": {
                "chars": len(deliberation),
                "approx_tokens": _approx_tokens(deliberation),
            },
            "full_deliberation_context": {
                "chars": len(full),
                "approx_tokens": _approx_tokens(full),
            },
        }
    return metrics


def _graph_only_notice() -> str:
    return (
        "## Ablation Context\n\n"
        "This run intentionally omits manuscript markdown and provides only graph-derived context. "
        "Use it only to measure what the graph alone can support. Do not infer that absent graph "
        "facts are absent from the paper."
    )


def _build_graph_policy_context(quality: dict[str, Any]) -> str:
    if quality["relationship_facts_usable"]:
        body = (
            "Graph relationship extraction passed the review-quality threshold. "
            "Use the graph as a factual index and use only positive relationship facts "
            "listed in Structured Graph Analysis. Do not infer paper weaknesses from "
            "missing graph edges, and do not cite internal graph counts in author-facing prose."
        )
    elif quality["mode"] == "index_only":
        body = (
            "Graph relationship extraction did not meet the review-quality threshold. "
            "Use the graph only as a manuscript section/entity index for navigation. "
            "Do not cite graph edge counts, missing evidence links, baseline edges, "
            "connectivity, or graph unsupportedness as evidence about the paper."
        )
    else:
        body = (
            "No usable knowledge graph is available. Treat manuscript text and verified "
            "figure/table annotations as the primary evidence source."
        )
    return _wrap_section("Knowledge Graph Use Policy", body)


def _build_review_graph_analysis(
    graph: KnowledgeGraph,
    quality: dict[str, Any],
) -> str:
    """Generate reviewer-safe structured graph analysis."""
    if quality["mode"] == "unavailable":
        return _wrap_section(
            "Structured Graph Analysis",
            "No knowledge graph entities are available for this session.",
        )

    semantic = _semantic_nodes(graph)
    if quality["mode"] == "index_only":
        sections = [s for s in graph.section_names if s]
        semantic_types = sorted({n.node_type for n in semantic})
        body_parts = [
            "The graph is usable only as a section/entity index. Relationship facts are not reviewer evidence."
        ]
        if sections:
            body_parts.append("Indexed sections: " + ", ".join(sections[:12]))
        if semantic_types:
            body_parts.append("Indexed entity types: " + ", ".join(semantic_types[:12]))
        return _wrap_section("Structured Graph Analysis", "\n".join(body_parts))

    if not semantic:
        return _wrap_section(
            "Structured Graph Analysis",
            "The graph contains only structural paper nodes and no semantic claim, method, baseline, result, or dataset entities.",
        )

    typed: dict[str, list[GraphNode]] = defaultdict(list)
    for node in semantic:
        typed[node.node_type].append(node)
    node_labels = {n.id: n.label for n in graph.nodes}
    semantic_edges = _semantic_edges(graph)

    lines = [
        "Use only the relationship facts and entity ledgers listed here. Figure and table references "
        "are usable only when the same figure or table appears in the manuscript text or extracted annotations."
    ]

    if semantic_edges:
        lines.append("\n### Extracted Relationship Facts")
        for edge in semantic_edges[:12]:
            src = node_labels.get(edge.source_id, edge.source_id)
            tgt = node_labels.get(edge.target_id, edge.target_id)
            lines.append(f"- {src[:90]} --[{edge.edge_type}]--> {tgt[:90]}")

    _append_entity_group(lines, "Claims", typed.get("Claim", []), limit=6)
    _append_entity_group(lines, "Methods", typed.get("Method", []), limit=8)

    evaluation_groups = [
        ("Baselines", typed.get("Baseline", [])),
        ("Workloads", typed.get("Workload", []) or typed.get("Dataset", [])),
        ("Metrics", typed.get("Metric", [])),
        ("Results", typed.get("Result", [])),
    ]
    if any(nodes for _, nodes in evaluation_groups):
        lines.append("\n### Evaluation Entities")
        for label, nodes in evaluation_groups:
            if nodes:
                values = ", ".join(_short_label(n.label) for n in nodes[:8])
                lines.append(f"- {label}: {values}")

    section_counts: dict[str, int] = {}
    for node in semantic:
        if node.source_section:
            section_counts[node.source_section] = section_counts.get(node.source_section, 0) + 1
    if section_counts:
        lines.append("\n### Section Entity Coverage")
        for section, count in sorted(section_counts.items(), key=lambda item: -item[1])[:8]:
            lines.append(f"- {section}: {count} entities")

    result = "\n".join(lines) + "\n"
    if len(result) > 3600:
        result = result[:3400].rsplit("\n", 1)[0] + "\n"
    return _wrap_section("Structured Graph Analysis", result)


def _append_entity_group(
    lines: list[str],
    heading: str,
    nodes: list[GraphNode],
    *,
    limit: int,
) -> None:
    if not nodes:
        return
    lines.append(f"\n### {heading}")
    for node in nodes[:limit]:
        section = f" [{node.source_section}]" if node.source_section else ""
        lines.append(f"- {_short_label(node.label)[:100]}{section}")


def _short_label(label: str) -> str:
    cleaned = label.strip()
    for sep in (":", "(", " - "):
        if sep in cleaned:
            short = cleaned.split(sep)[0].strip()
            if len(short) > 2:
                return short
    return cleaned


def _build_visual_evidence_ledger(graph: KnowledgeGraph) -> str:
    """Concise, reviewer-safe ledger of figure/table/equation evidence."""
    visual = [
        n for n in graph.nodes
        if n.node_type in ("Figure", "Table") and n.attributes.get("description")
    ]
    equations = [
        n for n in graph.nodes
        if n.node_type == "Equation"
        and n.attributes.get("grounding") in {"formula_not_decoded", "formula_decoded"}
    ]
    if not visual and not equations:
        return ""

    lines = []
    if visual:
        lines.append(
            "Vision-model descriptions of figures/tables. Treat numeric claims as extracted-from-figure "
            "and cross-check against manuscript text."
        )
        for node in visual[:12]:
            page = node.attributes.get("page", "?")
            claims = node.attributes.get("numeric_claims") or []
            claim_text = f" Numeric: {'; '.join(claims[:4])}." if claims else ""
            lines.append(
                f"- {node.label[:70]} (p.{page}): "
                f"{node.attributes['description'][:260]}{claim_text}"
            )
    if equations:
        if visual:
            lines.append("\n### Equation Evidence")
        lines.append(
            "Equation extraction notes. A not-decoded equation means the formula was present "
            "but the parser did not recover its formula text; do not quote it as decoded."
        )
        for node in equations[:12]:
            page = node.attributes.get("page", "?")
            context = node.attributes.get("surrounding_context") or {}
            before = context.get("before", "") if isinstance(context, dict) else ""
            after = context.get("after", "") if isinstance(context, dict) else ""
            status = "decoded" if node.attributes.get("grounding") == "formula_decoded" else "not decoded"
            snippet = node.source_text if status == "decoded" else (before or after or node.source_text)
            lines.append(f"- {node.label[:70]} ({status}, p.{page}): {str(snippet)[:240]}")
    return _wrap_section("Visual Evidence Ledger", "\n".join(lines))


def _required_citation_instructions() -> str:
    return _wrap_section(
        "Evidence Citation Requirement",
        (
            "For every substantive strength, weakness, question, and recommendation rationale, "
            "populate the citations array. Use section, figure, table, or page fields for manuscript "
            "evidence. When relying on graph context, put the graph entity label or relationship fact "
            "in graph_ref. If a claim has no support in either manuscript or graph context, mark that "
            "absence explicitly instead of filling graph_ref with a guess."
        ),
    )


def _graph_metrics(graph: KnowledgeGraph, quality: dict[str, Any]) -> dict[str, Any]:
    by_type: dict[str, int] = defaultdict(int)
    semantic_by_type: dict[str, int] = defaultdict(int)
    for node in graph.nodes:
        by_type[node.node_type] += 1
        if node.node_type not in STRUCTURAL_NODE_TYPES:
            semantic_by_type[node.node_type] += 1
    stats = graph.graph_stats()
    return {
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "node_count_by_type": dict(sorted(by_type.items())),
        "semantic_node_count_by_type": dict(sorted(semantic_by_type.items())),
        "stats": stats,
        "grounding": {
            key: quality[key]
            for key in (
                "grounding_mode",
                "visual_evidence_count",
                "described_artifact_count",
                "undescribed_artifact_count",
                "equation_evidence_count",
                "decoded_equation_count",
                "not_decoded_equation_count",
                "total_evidence_artifact_count",
            )
            if key in quality
        },
    }
