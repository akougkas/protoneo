"""Unified knowledge graph.

The KnowledgeGraph is the single source of truth for a session's document
analysis. Created during document processing, enriched during extraction,
queried during deliberation. Persisted as part of the session JSON.
"""

import uuid as _uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .ontology import Ontology


class GraphAnnotation(BaseModel):
    """An annotation added by an agent or during deliberation."""

    agent_id: str
    annotation_type: str  # "strength", "weakness", "question", "consensus"
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class GraphNode(BaseModel):
    """A node in the knowledge graph."""

    id: str = Field(default_factory=lambda: _uuid.uuid4().hex[:12])
    label: str
    node_type: str  # From ontology: "Method", "Dataset", "Claim", etc.
    description: str = ""
    source_section: str = ""
    source_text: str = ""
    confidence: float = 1.0
    attributes: dict[str, Any] = Field(default_factory=dict)
    annotations: list[GraphAnnotation] = Field(default_factory=list)


class GraphEdge(BaseModel):
    """A directed edge in the knowledge graph."""

    source_id: str
    target_id: str
    edge_type: str  # From ontology: "uses", "evaluates", "extends", etc.
    description: str = ""
    confidence: float = 1.0
    source_text: str = ""


class KnowledgeGraph(BaseModel):
    """The unified knowledge graph for a session.

    Created during document processing, enriched during extraction,
    queried during deliberation. Persisted as part of the session JSON.
    """

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    ontology: Ontology | None = None
    summary: str = ""

    paper_title: str = ""
    paper_abstract: str = ""
    section_names: list[str] = Field(default_factory=list)

    node_count_by_type: dict[str, int] = Field(default_factory=dict)
    edge_count_by_type: dict[str, int] = Field(default_factory=dict)

    # ── Mutation methods ─────────────────────────────────────

    def add_node(
        self,
        label: str,
        node_type: str,
        node_id: str | None = None,
        **kwargs: Any,
    ) -> GraphNode:
        """Add a node, deduplicating by (label, node_type).

        Also checks for label-only matches: if the same label exists with a
        different type, prefer the more specific type (anything > "Concept").
        """
        label_lower = label.lower()
        # Exact match: same label and type
        for existing in self.nodes:
            if existing.label.lower() == label_lower and existing.node_type == node_type:
                return existing

        # Label-only match: same label, different type
        _GENERIC = {"Concept", "Reference", "Equation"}
        for existing in self.nodes:
            if existing.label.lower() == label_lower:
                # If existing is generic and new is specific, upgrade the type
                if existing.node_type in _GENERIC and node_type not in _GENERIC:
                    existing.node_type = node_type
                    if kwargs.get("description") and not existing.description:
                        existing.description = kwargs["description"]
                    if kwargs.get("source_section") and not existing.source_section:
                        existing.source_section = kwargs["source_section"]
                return existing

        node = GraphNode(
            id=node_id or _uuid.uuid4().hex[:12],
            label=label,
            node_type=node_type,
            **kwargs,
        )
        self.nodes.append(node)
        return node

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        **kwargs: Any,
    ) -> GraphEdge:
        """Add an edge, deduplicating by (source, target, type)."""
        for existing in self.edges:
            if (
                existing.source_id == source_id
                and existing.target_id == target_id
                and existing.edge_type == edge_type
            ):
                return existing
        edge = GraphEdge(
            source_id=source_id,
            target_id=target_id,
            edge_type=edge_type,
            **kwargs,
        )
        self.edges.append(edge)
        return edge

    def annotate_node(
        self,
        node_id: str,
        agent_id: str,
        annotation_type: str,
        content: str,
    ) -> None:
        """Attach a review annotation to a node."""
        for node in self.nodes:
            if node.id == node_id:
                node.annotations.append(
                    GraphAnnotation(
                        agent_id=agent_id,
                        annotation_type=annotation_type,
                        content=content,
                    )
                )
                return

    def remove_node(self, node_id: str) -> GraphNode | None:
        """Remove a node and all edges referencing it. Returns the removed node."""
        target = None
        for i, n in enumerate(self.nodes):
            if n.id == node_id:
                target = self.nodes.pop(i)
                break
        if target is None:
            return None
        self.edges = [
            e for e in self.edges
            if e.source_id != node_id and e.target_id != node_id
        ]
        return target

    def redirect_edges(self, old_node_id: str, new_node_id: str) -> int:
        """Redirect all edges from old_node_id to new_node_id. Returns count."""
        count = 0
        for e in self.edges:
            if e.source_id == old_node_id:
                e.source_id = new_node_id
                count += 1
            if e.target_id == old_node_id:
                e.target_id = new_node_id
                count += 1
        return count

    def node_by_id(self, node_id: str) -> GraphNode | None:
        for n in self.nodes:
            if n.id == node_id:
                return n
        return None

    def node_by_label(self, label: str) -> GraphNode | None:
        """Find a node by case-insensitive label match."""
        label_lower = label.lower()
        for n in self.nodes:
            if n.label.lower() == label_lower:
                return n
        return None

    def get_accumulated_context(self) -> str:
        """Rich summary of all entities and relationships for section-aware extraction.

        Returns entities with full descriptions and their key relationships so
        later sections understand the scientific meaning of earlier extractions.
        Skips structural nodes (Paper, Section, Diagram, Table).
        """
        _STRUCTURAL = {"Paper", "Section", "Diagram", "Table"}
        node_map = {n.id: n for n in self.nodes}

        entity_lines = []
        for n in self.nodes:
            if n.node_type in _STRUCTURAL:
                continue
            desc = n.description if n.description else ""
            entity_lines.append(f"- {n.label} ({n.node_type}): {desc}")

        rel_lines = []
        for e in self.edges:
            if e.edge_type in ("APPEARS_IN", "HAS_SECTION", "CONTAINS"):
                continue
            src = node_map.get(e.source_id)
            tgt = node_map.get(e.target_id)
            if src and tgt and src.node_type not in _STRUCTURAL and tgt.node_type not in _STRUCTURAL:
                desc = f" ({e.description})" if e.description else ""
                rel_lines.append(f"- {src.label} --[{e.edge_type}]--> {tgt.label}{desc}")

        parts = []
        if entity_lines:
            parts.append("Entities:\n" + "\n".join(entity_lines))
        if rel_lines:
            parts.append("Relationships:\n" + "\n".join(rel_lines))
        return "\n\n".join(parts)

    def update_stats(self) -> None:
        """Recompute node/edge counts by type."""
        nc: dict[str, int] = defaultdict(int)
        for n in self.nodes:
            nc[n.node_type] += 1
        self.node_count_by_type = dict(nc)

        ec: dict[str, int] = defaultdict(int)
        for e in self.edges:
            ec[e.edge_type] += 1
        self.edge_count_by_type = dict(ec)

    # ── Ontology ingestion ─────────────────────────────────

    def add_ontology_nodes(self, ontology: Ontology) -> None:
        """Create Concept nodes from ontology key_contributions.

        Links each contribution to the paper root via PART_OF edges.
        Stores the ontology on the graph.
        """
        self.ontology = ontology
        root_id = "paper-root"
        for contrib in (ontology.key_contributions or []):
            node = self.add_node(
                label=contrib,
                node_type="Concept",
                description=f"Key contribution: {contrib}",
                source_section="ontology",
            )
            self.add_edge(root_id, node.id, "PART_OF", description="Key contribution")
        self.update_stats()

    # ── Snapshot / restore ───────────────────────────────────

    def snapshot(self) -> dict:
        """Return a serializable snapshot for step-level persistence."""
        return self.model_dump(mode="json")

    @classmethod
    def restore_from_snapshot(cls, data: dict) -> "KnowledgeGraph":
        """Restore a KnowledgeGraph from a snapshot dict."""
        return cls.model_validate(data)

    # ── Ingestion methods ────────────────────────────────────

    def ingest_metadata(self, metadata: Any) -> None:
        """Build structural nodes from heuristic DocumentMetadata.

        Creates Paper root, Section nodes, Figure and Table nodes.
        No LLM required.
        """
        self.paper_title = metadata.title or ""
        self.paper_abstract = metadata.abstract or ""
        self.section_names = list(metadata.sections)

        root = self.add_node(
            label=metadata.title or "Untitled Paper",
            node_type="Paper",
            node_id="paper-root",
            attributes={
                "word_count": str(metadata.estimated_word_count),
                "reference_count": str(metadata.reference_count),
            },
        )

        if metadata.abstract:
            abs_node = self.add_node(
                label="Abstract",
                node_type="Section",
                attributes={"text_preview": metadata.abstract[:200]},
            )
            self.add_edge(root.id, abs_node.id, "HAS_SECTION")

        for i, sec in enumerate(metadata.sections):
            sec_node = self.add_node(
                label=sec,
                node_type="Section",
                attributes={"order": str(i + 1)},
            )
            self.add_edge(root.id, sec_node.id, "HAS_SECTION")

        for i in range(1, min(metadata.figure_count + 1, 16)):
            fig = self.add_node(label=f"Figure {i}", node_type="Diagram")
            self.add_edge(root.id, fig.id, "CONTAINS")

        for i in range(1, min(metadata.table_count + 1, 16)):
            tbl = self.add_node(label=f"Table {i}", node_type="Table")
            self.add_edge(root.id, tbl.id, "CONTAINS")

        # Equation nodes (from NLP pre-pass)
        if hasattr(metadata, 'equation_labels'):
            for eq_label in metadata.equation_labels:
                eq = self.add_node(label=eq_label, node_type="Equation")
                self.add_edge(root.id, eq.id, "CONTAINS")

        # Store references as metadata on the paper root, not as individual nodes.
        # Individual Reference nodes bloat the graph (50+ entries) without adding
        # semantic value for agents. The reference count is already an attribute.
        if hasattr(metadata, 'references') and metadata.references:
            root_node = self.node_by_id("paper-root")
            if root_node:
                root_node.attributes["reference_sample"] = "; ".join(
                    metadata.references[:5]
                )

        # Pending citation edges from citation markers.
        # These link the section where a citation appears to the Reference node.
        # The edges are "pending" because full semantic entities do not exist yet.
        for cm in getattr(metadata, "citation_markers", []):
            marker = cm.get("marker", "")
            section_name = cm.get("section", "")
            # Find section node
            sec_node = self.node_by_label(section_name) if section_name else None
            # Find matching reference node by citation key
            ref_node = None
            if marker.startswith("["):
                for n in self.nodes:
                    if n.node_type == "Reference" and marker in n.label[:20]:
                        ref_node = n
                        break
            if sec_node and ref_node:
                self.add_edge(
                    sec_node.id,
                    ref_node.id,
                    "CITES",
                    description=f"citation {marker} in {section_name}",
                    confidence=0.8,
                )

        self.update_stats()

    def ingest_d3_data(self, data: dict[str, Any]) -> None:
        """Absorb nodes and edges from GraphPanel / D3 format.

        Handles ID mapping when deduplication merges a D3 node
        into an existing KnowledgeGraph node.
        """
        d3_to_pg: dict[str, str] = {}

        for nd in data.get("nodes", []):
            if not isinstance(nd, dict):
                continue
            raw_id = nd.get("uuid") or nd.get("id")
            label = nd.get("name") or nd.get("label") or nd.get("display_name") or raw_id
            if not raw_id or not label:
                continue
            labels = nd.get("labels", [])
            node_type = (
                nd.get("type")
                or nd.get("node_type")
                or next((l for l in labels if l != "Entity"), "Concept")
            )
            attrs = dict(nd.get("attributes", {}))
            desc = attrs.pop("description", nd.get("description", ""))
            if nd.get("display_name") and "display_name" not in attrs:
                attrs["display_name"] = nd["display_name"]
            node = self.add_node(
                label=str(label),
                node_type=str(node_type or "Concept"),
                node_id=str(raw_id),
                description=desc,
                attributes=attrs,
            )
            d3_to_pg[str(raw_id)] = node.id

        def _edge_endpoint(value: Any) -> str:
            if isinstance(value, dict):
                return str(value.get("uuid") or value.get("id") or "")
            return str(value or "")

        for ed in data.get("edges", data.get("links", [])):
            if not isinstance(ed, dict):
                continue
            source_raw = (
                ed.get("source_node_uuid")
                or ed.get("source_id")
                or _edge_endpoint(ed.get("source"))
            )
            target_raw = (
                ed.get("target_node_uuid")
                or ed.get("target_id")
                or _edge_endpoint(ed.get("target"))
            )
            src = d3_to_pg.get(str(source_raw))
            tgt = d3_to_pg.get(str(target_raw))
            if src and tgt:
                attrs = ed.get("attributes", {}) or {}
                if not isinstance(attrs, dict):
                    attrs = {}
                self.add_edge(
                    source_id=src,
                    target_id=tgt,
                    edge_type=(
                        ed.get("name")
                        or ed.get("fact_type")
                        or ed.get("edge_type")
                        or ed.get("type")
                        or "RELATED_TO"
                    ),
                    description=attrs.get("description", ed.get("description", "")),
                )

        self.update_stats()

    # ── Export methods ───────────────────────────────────────

    def to_d3_format(self) -> dict[str, Any]:
        """Convert to GraphPanel-compatible D3 format.

        Each node includes an explicit 'type' field (the semantic entity type)
        and a numeric 'group' for D3 force-graph coloring, in addition to the
        standard 'labels' array.
        """
        all_types = sorted({n.node_type for n in self.nodes})
        type_to_group = {t: i for i, t in enumerate(all_types)}

        def _display_name(label: str) -> str:
            """Extract a short display name (under 50 chars) from a verbose label."""
            for sep in (":", "(", " — "):
                if sep in label:
                    short = label.split(sep)[0].strip()
                    if len(short) > 2:
                        return short[:50]
            return label[:50]

        nodes = []
        for n in self.nodes:
            nodes.append(
                {
                    "uuid": n.id,
                    "name": n.label,
                    "display_name": _display_name(n.label),
                    "type": n.node_type,
                    "group": type_to_group.get(n.node_type, 0),
                    "labels": ["Entity", n.node_type],
                    "attributes": {"description": n.description, **n.attributes},
                }
            )

        edges = []
        for e in self.edges:
            edges.append(
                {
                    "source_node_uuid": e.source_id,
                    "target_node_uuid": e.target_id,
                    "name": e.edge_type,
                    "fact_type": e.edge_type,
                    "attributes": {"description": e.description},
                }
            )

        return {"nodes": nodes, "edges": edges}

    def to_agent_briefing(self, domain_config: Any = None) -> str:
        """Concise briefing derived from the knowledge graph for agent context.

        Structured as three sections that directly support review writing:
        key claims, methodology concerns, and evaluation summary. Capped
        at ~5000 chars to avoid overwhelming agents.
        """
        if not self.nodes:
            return ""

        self.update_stats()

        _STRUCTURAL = {"Paper", "Section", "Diagram", "Table", "Reference", "Equation"}
        _NON_SEMANTIC_RELS = {"HAS_SECTION", "CONTAINS", "APPEARS_IN"}

        semantic = [n for n in self.nodes if n.node_type not in _STRUCTURAL]
        if not semantic:
            return ""

        typed: dict[str, list[GraphNode]] = defaultdict(list)
        for n in semantic:
            typed[n.node_type].append(n)

        label_map = {n.id: n.label for n in self.nodes}
        sem_edges = [e for e in self.edges if e.edge_type not in _NON_SEMANTIC_RELS]
        stats = self.graph_stats()

        lines = [
            f"\n\n## Knowledge Graph Briefing"
            f" ({stats['semantic_entities']} entities, "
            f"{stats['connectivity_ratio']:.0%} connected)\n"
        ]

        # ── Section 1: Key Claims ──
        claims = typed.get("Claim", [])
        methods = typed.get("Method", [])
        contributions = (
            self.ontology.key_contributions if self.ontology and self.ontology.key_contributions else []
        )

        lines.append("### Key Claims & Contributions")
        if contributions:
            for c in contributions[:7]:
                lines.append(f"- {c}")
        for claim in claims[:5]:
            sec = f" [{claim.source_section}]" if claim.source_section else ""
            lines.append(f"- {claim.label}{sec}")
        if not contributions and not claims:
            for m in methods[:5]:
                sec = f" [{m.source_section}]" if m.source_section else ""
                lines.append(f"- Method: {m.label}{sec}")

        # ── Section 2: Methodology & Grounding ──
        lines.append("\n### Methodology")
        # Show methods and what they use/evaluate
        method_edges = [e for e in sem_edges if e.edge_type in ("USES", "EVALUATES_ON", "COMPARED_AGAINST")]
        shown = set()
        for e in method_edges[:8]:
            src = label_map.get(e.source_id, "?")
            tgt = label_map.get(e.target_id, "?")
            key = (src, e.edge_type, tgt)
            if key not in shown:
                lines.append(f"- {src} {e.edge_type.lower().replace('_', ' ')} {tgt}")
                shown.add(key)
        if not method_edges:
            for m in methods[:4]:
                lines.append(f"- {m.label}")

        # ── Section 3: Evaluation Summary ──
        results = typed.get("Result", [])
        baselines = typed.get("Baseline", [])
        metrics = typed.get("Metric", [])
        datasets = typed.get("Dataset", [])

        lines.append("\n### Evaluation")
        if baselines:
            names = ", ".join(b.label.split(":")[0].strip() for b in baselines[:6])
            lines.append(f"- Baselines: {names}")
        if metrics:
            names = ", ".join(m.label.split(":")[0].strip() for m in metrics[:8])
            lines.append(f"- Metrics: {names}")
        if datasets:
            names = ", ".join(d.label.split(":")[0].strip() for d in datasets[:5])
            lines.append(f"- Datasets/workloads: {names}")
        for r in results[:5]:
            sec = f" [{r.source_section}]" if r.source_section else ""
            lines.append(f"- Result: {r.label[:80]}{sec}")

        # Figures/tables referenced by semantic entities
        _VISUAL_TYPES = {"Diagram", "Table"}
        visual_nodes = {n.id: n for n in self.nodes if n.node_type in _VISUAL_TYPES}
        if visual_nodes:
            referenced_visuals: list[str] = []
            for e in sem_edges:
                if e.source_id in visual_nodes:
                    tgt = label_map.get(e.target_id, "")
                    if tgt:
                        referenced_visuals.append(f"{visual_nodes[e.source_id].label} ({tgt})")
                elif e.target_id in visual_nodes:
                    src = label_map.get(e.source_id, "")
                    if src:
                        referenced_visuals.append(f"{visual_nodes[e.target_id].label} ({src})")
            if referenced_visuals:
                lines.append(f"- Evidence artifacts: {', '.join(referenced_visuals[:6])}")

        # ── Section 4: Limitations ──
        limitations = typed.get("Limitation", [])
        if limitations:
            lines.append("\n### Limitations")
            for lim in limitations[:5]:
                sec = f" [{lim.source_section}]" if lim.source_section else ""
                lines.append(f"- {lim.label[:80]}{sec}")

        # ── Section 5: Potential Concerns ──
        low_confidence = [n for n in semantic if n.confidence < 0.5]
        if low_confidence:
            lines.append("\n### Potential Concerns")
            lines.append("*Entities flagged by verification (confidence < 0.5):*")
            for n in sorted(low_confidence, key=lambda x: x.confidence)[:8]:
                lines.append(
                    f"- {n.label} ({n.node_type}, confidence={n.confidence:.2f})"
                )

        # ── Coverage stats (compact) ──
        lines.append(
            f"\n*Graph: {stats['entity_types']} entity types, "
            f"{stats['edge_types']} edge types, "
            f"{stats['sections_covered']}/{stats['total_sections']} sections covered.*"
        )

        summary = "\n".join(lines) + "\n"
        # Cap at ~5000 chars to accommodate the additional sections
        if len(summary) > 5200:
            summary = summary[:5000].rsplit("\n", 1)[0] + "\n"
        return summary

    # ── Review annotation ────────────────────────────────────

    def annotate_from_review(self, review_data: dict, agent_id: str) -> int:
        """Map review findings to graph nodes and attach annotations.

        Uses simple string matching: if a strength/weakness/question
        mentions an entity label, that entity gets the annotation.
        Unmatched findings annotate the paper root node.

        Returns the number of annotations added.
        """
        root_id = "paper-root"
        name_to_id: dict[str, str] = {}
        for n in self.nodes:
            if len(n.label) > 3:
                name_to_id[n.label.lower()] = n.id

        count = 0

        def _find_node(text: str) -> str:
            text_lower = text.lower()
            for name, nid in name_to_id.items():
                if name in text_lower:
                    return nid
            return root_id

        def _text(item: Any) -> str:
            if isinstance(item, str):
                return item
            if isinstance(item, dict):
                return item.get("text", item.get("description", str(item)))
            return str(item)

        for kind in ("strengths", "weaknesses", "questions_for_authors"):
            ann_type = kind.rstrip("s").replace("questions_for_author", "question")
            for item in review_data.get(kind, []):
                text = _text(item)
                target = _find_node(text)
                self.annotate_node(target, agent_id, ann_type, text)
                count += 1

        return count

    # ── Utilization analysis ────────────────────────────────

    def compute_utilization(self, agent_outputs: list[dict]) -> dict[str, Any]:
        """Compute how well agents utilized the knowledge graph.

        Deterministic (no LLM). Scans agent output text for entity label substrings.

        Args:
            agent_outputs: list of dicts with at least "agent_id" and text fields
                           (strengths, weaknesses, questions_for_authors, summary,
                            comments_for_authors, raw_content).

        Returns dict with per_entity, per_reviewer, unreferenced_entities,
        utilization_ratio, and by_type breakdowns.
        """
        _STRUCTURAL = {"Paper", "Section", "Diagram", "Table", "Reference", "Equation"}
        semantic = [n for n in self.nodes if n.node_type not in _STRUCTURAL and len(n.label) > 3]

        if not semantic:
            return {
                "per_entity": [],
                "per_reviewer": {},
                "unreferenced_entities": [],
                "utilization_ratio": 0.0,
                "by_type": {},
            }

        def _extract_text(review: dict) -> str:
            parts = []
            for key in ("summary", "comments_for_authors", "raw_content"):
                val = review.get(key, "")
                if val:
                    parts.append(str(val))
            for key in ("strengths", "weaknesses", "questions_for_authors"):
                items = review.get(key, [])
                for item in items:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        parts.append(item.get("text", item.get("point", item.get("description", ""))))
            return " ".join(parts).lower()

        reviewer_texts: dict[str, str] = {}
        for output in agent_outputs:
            aid = output.get("agent_id", output.get("reviewer_role", "unknown"))
            reviewer_texts[aid] = _extract_text(output)

        per_entity: list[dict] = []
        referenced_ids: set[str] = set()
        type_coverage: dict[str, dict] = defaultdict(lambda: {"total": 0, "referenced": 0})

        def _match_aliases(node: GraphNode) -> list[str]:
            """Generate match aliases for a node: full label, short name, key terms."""
            aliases = set()
            label = node.label.strip()
            aliases.add(label.lower())
            # Short name: text before first colon or parenthesis
            for sep in (":", "(", " — "):
                if sep in label:
                    short = label.split(sep)[0].strip()
                    if len(short) > 2:
                        aliases.add(short.lower())
                    break
            # Node name field may differ from label (for ingested D3 data)
            if hasattr(node, "attributes") and node.attributes.get("short_name"):
                aliases.add(node.attributes["short_name"].lower())
            return [a for a in aliases if len(a) > 2]

        for node in semantic:
            match_strings = _match_aliases(node)
            mentioning_reviewers = []
            for aid, text in reviewer_texts.items():
                if any(alias in text for alias in match_strings):
                    mentioning_reviewers.append(aid)
            per_entity.append({
                "entity_id": node.id,
                "label": node.label,
                "type": node.node_type,
                "mentioned_by": mentioning_reviewers,
                "mention_count": len(mentioning_reviewers),
            })
            type_coverage[node.node_type]["total"] += 1
            if mentioning_reviewers:
                referenced_ids.add(node.id)
                type_coverage[node.node_type]["referenced"] += 1

        unreferenced = [
            {"entity_id": e["entity_id"], "label": e["label"], "type": e["type"]}
            for e in per_entity if e["mention_count"] == 0
        ]

        per_reviewer: dict[str, dict] = {}
        for aid in reviewer_texts:
            entities_covered = sum(
                1 for e in per_entity
                if aid in e["mentioned_by"]
            )
            per_reviewer[aid] = {
                "entities_covered": entities_covered,
                "total_entities": len(semantic),
                "coverage_ratio": round(entities_covered / max(len(semantic), 1), 3),
            }

        by_type: dict[str, dict] = {}
        for etype, counts in type_coverage.items():
            by_type[etype] = {
                "total": counts["total"],
                "referenced": counts["referenced"],
                "ratio": round(counts["referenced"] / max(counts["total"], 1), 3),
            }

        return {
            "per_entity": per_entity,
            "per_reviewer": per_reviewer,
            "unreferenced_entities": unreferenced,
            "utilization_ratio": round(len(referenced_ids) / max(len(semantic), 1), 3),
            "by_type": by_type,
        }

    # ── Internal helpers ─────────────────────────────────────

    def prune_orphans(self) -> int:
        """Remove semantic entities with no edges. Returns count removed.

        Orphaned entities add noise without helping agents. Structural
        nodes (Paper, Section, etc.) are never pruned.
        """
        _KEEP = {"Paper", "Section", "Diagram", "Table", "Reference", "Equation"}
        connected = set()
        for e in self.edges:
            connected.add(e.source_id)
            connected.add(e.target_id)

        before = len(self.nodes)
        self.nodes = [
            n for n in self.nodes
            if n.node_type in _KEEP or n.id in connected
        ]
        removed = before - len(self.nodes)
        if removed:
            self.update_stats()
        return removed

    def prune_ungrounded(self, threshold: float = 0.3) -> int:
        """Remove only low-confidence nodes below threshold. Returns count removed.

        Unlike prune_orphans which removes all disconnected nodes, this
        preserves orphans that are well-grounded (high confidence) and only
        removes nodes flagged as potentially hallucinated by the verification
        pass. Structural nodes are never pruned.
        """
        _KEEP = {"Paper", "Section", "Diagram", "Table", "Reference", "Equation"}
        kept: list[GraphNode] = []
        removed_ids: set[str] = set()
        for n in self.nodes:
            if n.node_type in _KEEP or n.confidence >= threshold:
                kept.append(n)
            else:
                removed_ids.add(n.id)
        self.nodes = kept
        if removed_ids:
            self.edges = [
                e for e in self.edges
                if e.source_id not in removed_ids and e.target_id not in removed_ids
            ]
        removed = len(removed_ids)
        if removed:
            self.update_stats()
        return removed

    def ensure_structural_links(self) -> int:
        """Create missing APPEARS_IN edges from semantic nodes to their source sections.

        Scans all semantic nodes that have a source_section field and creates
        APPEARS_IN edges to the matching Section node if one does not already
        exist. This bridges the structural and semantic subgraphs.

        Returns the number of APPEARS_IN edges created.
        """
        _STRUCTURAL = {"Paper", "Section", "Diagram", "Table", "Reference", "Equation"}

        # Build section label to node ID map
        section_map: dict[str, str] = {}
        for n in self.nodes:
            if n.node_type == "Section":
                section_map[n.label.lower()] = n.id

        # Build existing APPEARS_IN set for dedup
        existing = {
            (e.source_id, e.target_id)
            for e in self.edges if e.edge_type == "APPEARS_IN"
        }

        created = 0
        for n in self.nodes:
            if n.node_type in _STRUCTURAL or not n.source_section:
                continue
            sec_id = section_map.get(n.source_section.lower())
            if not sec_id:
                # Try partial match (section name might be a substring)
                for sec_label, sid in section_map.items():
                    if n.source_section.lower() in sec_label or sec_label in n.source_section.lower():
                        sec_id = sid
                        break
            if sec_id and (n.id, sec_id) not in existing:
                self.add_edge(
                    source_id=n.id,
                    target_id=sec_id,
                    edge_type="APPEARS_IN",
                    description=f"extracted from {n.source_section}",
                )
                existing.add((n.id, sec_id))
                created += 1

        if created:
            self.update_stats()
        return created

    def graph_stats(self) -> dict[str, Any]:
        """Compute pure structural statistics about the graph.

        No opinions, no gap judgments. Just topology facts that describe
        what the graph contains and how connected it is.
        """
        _STRUCTURAL = {"Paper", "Section", "Diagram", "Table", "Reference", "Equation"}
        _STRUCTURAL_RELS = {"HAS_SECTION", "CONTAINS", "APPEARS_IN"}

        semantic = [n for n in self.nodes if n.node_type not in _STRUCTURAL]
        sem_edges = [e for e in self.edges if e.edge_type not in _STRUCTURAL_RELS]

        # Connectivity
        connected_ids = set()
        for e in sem_edges:
            connected_ids.add(e.source_id)
            connected_ids.add(e.target_id)
        connected_semantic = [n for n in semantic if n.id in connected_ids]

        # Sections covered
        sections_with_entities = set()
        for n in semantic:
            if n.source_section:
                sections_with_entities.add(n.source_section)

        # Edge type distribution
        edge_dist: dict[str, int] = defaultdict(int)
        for e in sem_edges:
            edge_dist[e.edge_type] += 1

        return {
            "semantic_entities": len(semantic),
            "semantic_edges": len(sem_edges),
            "connected_entities": len(connected_semantic),
            "connectivity_ratio": round(len(connected_semantic) / max(len(semantic), 1), 2),
            "sections_covered": len(sections_with_entities),
            "total_sections": len(self.section_names),
            "entity_types": len(set(n.node_type for n in semantic)),
            "edge_types": len(edge_dist),
            "edge_distribution": dict(sorted(edge_dist.items(), key=lambda x: -x[1])),
        }
