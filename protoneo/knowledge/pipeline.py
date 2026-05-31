"""Graph pipeline orchestrator.

Runs the 6-step knowledge graph generation pipeline:
metadata → ontology → extraction → coref → verification → summary.

The algorithm is kernel-owned. Domain expertise comes from DomainConfig.
Each step writes a durable checkpoint to the session. On resume, completed
stages are skipped based on existing checkpoints.
"""

import json
import logging
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..agents.types import Document
from ..api.events import SessionEventBus
from ..api.pipeline_control import PipelineControl
from ..deliberation.session import SessionManager, StageCheckpoint, StepState
from ..llm.client import LLMClient
from .coref_resolver import resolve_coreferences
from .graph import KnowledgeGraph
from .graph_extractor import extract_graph
from .graph_verifier import verify_graph
from .ontology import generate_ontology
from .types import DomainConfig

logger = logging.getLogger("protoneo.knowledge.pipeline")

KERNEL_STAGES = ["metadata", "ontology", "extraction", "coref", "verification", "summary"]


class GraphPipeline:
    """Orchestrates knowledge graph generation from a document.

    Runs: metadata → ontology → extraction → coref → verification → summary.
    The algorithm is kernel-owned. Domain expertise comes from DomainConfig.
    Each step writes a durable checkpoint to the session.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        session_manager: SessionManager,
        domain_config: DomainConfig | None = None,
    ):
        self.llm = llm_client
        self.sessions = session_manager
        self.domain = domain_config

    def _has_checkpoint(self, session: Any, stage_name: str) -> bool:
        """Check if a checkpoint exists for the given stage."""
        return any(cp.stage_name == stage_name for cp in session.checkpoints)

    def _write_checkpoint(self, session: Any, stage_name: str, output_key: str = "") -> None:
        """Write a checkpoint for the completed stage."""
        session.checkpoints.append(StageCheckpoint(
            stage_name=stage_name,
            completed_at=datetime.now(timezone.utc).isoformat(),
            output_key=output_key,
        ))
        session.last_checkpoint = stage_name

    async def run(
        self,
        session_id: str,
        document: Document,
        bus: SessionEventBus,
        ctl: PipelineControl,
        models: dict[str, str] | None = None,
        pruning_threshold: float = 0.3,
        conference_context: str = "",
        graph_cache: dict[str, dict] | None = None,
        ontology_cache: dict[str, Any] | None = None,
    ) -> KnowledgeGraph:
        """Run the 6-step graph pipeline with checkpoint-based resume.

        For each step:
        1. Check if checkpoint exists (skip if so)
        2. Check PipelineControl gates (pause if gated)
        3. Execute the step
        4. Write checkpoint to session
        5. Emit bus event

        Args:
            session_id: Session to operate on.
            document: Parsed document with text, markdown, chunks.
            bus: Event bus for progress updates.
            ctl: Pipeline control for pause/resume/cancel.
            models: Dict mapping step names to model IDs
                    (keys: ontology, extraction, coref, verification).
            pruning_threshold: Confidence threshold for pruning ungrounded entities.
            conference_context: Optional conference scope string for ontology generation.
            graph_cache: Shared in-memory D3 graph cache (keyed by session_id).
            ontology_cache: Shared in-memory ontology cache (keyed by session_id).

        Returns:
            The completed KnowledgeGraph.
        """
        from .metadata import extract_metadata, extract_metadata_from_markdown

        models = models or {}
        graph_cache = graph_cache if graph_cache is not None else {}
        ontology_cache = ontology_cache if ontology_cache is not None else {}

        session = await self.sessions.get(session_id)
        if not session:
            raise RuntimeError(f"Session {session_id} not found")

        # Restore graph from last checkpoint snapshot if resuming
        paper_graph = KnowledgeGraph()
        if session.checkpoints:
            last_cp = session.checkpoints[-1]
            # output_key format: "graph_after_step.<key>" or "knowledge_graph"
            snapshot = None
            if last_cp.output_key.startswith("graph_after_step."):
                step_key = last_cp.output_key.split(".", 1)[1]
                snapshot = session.graph_after_step.get(step_key)
            elif last_cp.output_key == "knowledge_graph" and session.knowledge_graph:
                snapshot = session.knowledge_graph
            if snapshot:
                paper_graph = KnowledgeGraph.restore_from_snapshot(snapshot)
                logger.info(
                    "Resuming from checkpoint '%s' (%d nodes, %d edges)",
                    last_cp.stage_name, len(paper_graph.nodes), len(paper_graph.edges),
                )

        ctl.enter_stage("pre_review")

        # ── Step 1: Parse ──────────────────────────────────
        ctl.enter_step("parse")
        step_start = _time.time()
        bus.emit("step_started", {
            "stage": "pre_review", "step": "parse",
            "message": "PDF parsed, extracting structure...",
        })
        session = await self.sessions.get(session_id)
        if session:
            session.pipeline_steps["parse"] = StepState(
                status="complete", started_at=step_start,
                completed_at=_time.time(),
            ).model_dump()
            await self.sessions.update(session)

        # ── Step 2: Metadata ───────────────────────────────
        if not self._has_checkpoint(session, "metadata"):
            ctl.enter_step("metadata")
            step_start = _time.time()
            bus.emit("step_started", {
                "stage": "pre_review", "step": "metadata",
                "message": "Running NLP pre-pass: metadata, citations, equations...",
            })

            if document.markdown:
                metadata = extract_metadata_from_markdown(document.markdown, document.text)
            else:
                metadata = extract_metadata(document.text)
            paper_graph.ingest_metadata(metadata)
            visual_added = paper_graph.ingest_visual_evidence(
                document.metadata.get("figures"),
                document.metadata.get("tables"),
            )
            if visual_added:
                artifacts = (
                    (document.metadata.get("figures") or [])
                    + (document.metadata.get("tables") or [])
                )
                bus.emit("visual_evidence_ingested", {
                    "figures": len(document.metadata.get("figures") or []),
                    "tables": len(document.metadata.get("tables") or []),
                    "described": sum(
                        1 for artifact in artifacts
                        if isinstance(artifact, dict) and artifact.get("description")
                    ),
                })

            bus.emit("metadata_extracted", {
                "title": metadata.title,
                "sections": metadata.sections,
                "figure_count": metadata.figure_count,
                "table_count": metadata.table_count,
                "reference_count": metadata.reference_count,
                "word_count": metadata.estimated_word_count,
                "citation_count": len(metadata.citation_markers),
                "equation_count": len(metadata.equation_labels),
            })

            d3_data = paper_graph.to_d3_format()
            graph_cache[session_id] = d3_data
            bus.emit("graph_updated", {
                "nodes": d3_data["nodes"], "edges": d3_data["edges"],
                "node_count": len(d3_data["nodes"]),
                "edge_count": len(d3_data["edges"]),
            })

            session = await self.sessions.get(session_id)
            if session:
                if metadata.title:
                    session.config.setdefault("metadata", {})["paper_title"] = metadata.title
                session.pipeline_steps["nlp_prepass"] = StepState(
                    status="complete", started_at=step_start,
                    completed_at=_time.time(),
                    nodes_added=len(paper_graph.nodes),
                    edges_added=len(paper_graph.edges),
                ).model_dump()
                session.graph_after_step["nlp_prepass"] = paper_graph.snapshot()
                self._write_checkpoint(session, "metadata", "graph_after_step.nlp_prepass")
                await self.sessions.update(session)
        else:
            metadata = extract_metadata(document.text)
            logger.info("Skipping metadata (checkpoint exists)")

        # ── Step 3: Ontology ───────────────────────────────
        if not self._has_checkpoint(session, "ontology"):
            ctl.enter_step("ontology")
            step_start = _time.time()
            bus.emit("step_started", {
                "stage": "pre_review", "step": "ontology",
                "message": f"Generating ontology for: {metadata.title[:80] if metadata.title else 'document'}...",
            })

            ontology_model = models.get("ontology", "")
            ontology = await generate_ontology(
                document.text, self.llm, model=ontology_model,
                session_id=session_id, conference_context=conference_context,
                metadata=metadata, markdown=document.markdown,
                domain_config=self.domain,
            )
            paper_graph.ontology = ontology
            paper_graph.add_ontology_nodes(ontology)
            ontology_cache[session_id] = ontology

            bus.emit("ontology_ready", {
                "entity_types": [et.model_dump() for et in ontology.entity_types],
                "edge_types": [rt.model_dump() for rt in ontology.edge_types],
                "paper_domain": ontology.paper_domain,
                "key_contributions": ontology.key_contributions,
                "analysis_summary": ontology.analysis_summary,
                "paused": not ctl.auto_advance,
            })

            session = await self.sessions.get(session_id)
            if session:
                session.pipeline_steps["ontology"] = StepState(
                    status="complete", started_at=step_start,
                    completed_at=_time.time(),
                    model_used=ontology_model,
                    nodes_added=len(paper_graph.nodes),
                    edges_added=len(paper_graph.edges),
                ).model_dump()
                session.graph_after_step["ontology"] = paper_graph.snapshot()
                self._write_checkpoint(session, "ontology", "graph_after_step.ontology")
                await self.sessions.update(session)

            await ctl.wait_if_paused()
            # Re-read ontology in case it was edited during pause
            if session_id in ontology_cache:
                ontology = ontology_cache[session_id]
                paper_graph.ontology = ontology
        else:
            ontology = ontology_cache.get(session_id)
            if not ontology and session.graph_after_step.get("ontology"):
                snap = session.graph_after_step["ontology"]
                pg_snap = KnowledgeGraph.restore_from_snapshot(snap)
                ontology = pg_snap.ontology
            logger.info("Skipping ontology (checkpoint exists)")

        # ── Step 4: Extraction ─────────────────────────────
        if not self._has_checkpoint(session, "extraction"):
            ctl.enter_step("extract")
            step_start = _time.time()
            nodes_before = len(paper_graph.nodes)
            bus.emit("step_started", {
                "stage": "pre_review", "step": "extract",
                "message": f"Extracting knowledge graph ({len(ontology.entity_types) if ontology else 0} entity types)...",
            })

            extraction_model = models.get("extraction", "")
            await extract_graph(
                document.text, self.llm, model=extraction_model,
                session_id=session_id,
                on_progress=lambda evt, data: bus.emit(evt, data),
                ontology=ontology,
                knowledge_graph=paper_graph,
                markdown=document.markdown,
            )

            graph_cache[session_id] = paper_graph.to_d3_format()

            bus.emit("graph_complete", {
                "node_count": len(paper_graph.nodes),
                "edge_count": len(paper_graph.edges),
            })

            session = await self.sessions.get(session_id)
            if session:
                session.pipeline_steps["extract"] = StepState(
                    status="complete", started_at=step_start,
                    completed_at=_time.time(),
                    model_used=extraction_model,
                    nodes_added=len(paper_graph.nodes) - nodes_before,
                    edges_added=len(paper_graph.edges),
                ).model_dump()
                session.graph_after_step["extract"] = paper_graph.snapshot()
                self._write_checkpoint(session, "extraction", "graph_after_step.extract")
                await self.sessions.update(session)
        else:
            logger.info("Skipping extraction (checkpoint exists)")

        # ── Step 5: Coreference Resolution ─────────────────
        if not self._has_checkpoint(session, "coref"):
            ctl.enter_step("coref")
            step_start = _time.time()
            bus.emit("step_started", {
                "stage": "pre_review", "step": "coref",
                "message": "Resolving co-references and linking abbreviations...",
            })

            coref_model = models.get("coref", "")
            coref_stats = await resolve_coreferences(
                paper_graph, self.llm,
                model=coref_model, session_id=session_id,
            )

            bus.emit("coref_complete", {
                "merged": coref_stats["merged"],
                "aliases_created": coref_stats["aliases_created"],
                "node_count": len(paper_graph.nodes),
                "edge_count": len(paper_graph.edges),
            })

            d3_data = paper_graph.to_d3_format()
            graph_cache[session_id] = d3_data
            bus.emit("graph_updated", {
                "nodes": d3_data["nodes"], "edges": d3_data["edges"],
                "node_count": len(d3_data["nodes"]),
                "edge_count": len(d3_data["edges"]),
            })

            session = await self.sessions.get(session_id)
            if session:
                session.pipeline_steps["coref"] = StepState(
                    status="complete", started_at=step_start,
                    completed_at=_time.time(),
                    model_used=coref_model,
                ).model_dump()
                session.graph_after_step["coref"] = paper_graph.snapshot()
                self._write_checkpoint(session, "coref", "graph_after_step.coref")
                await self.sessions.update(session)
        else:
            logger.info("Skipping coref (checkpoint exists)")

        # ── Step 6: Verification ───────────────────────────
        if not self._has_checkpoint(session, "verification"):
            ctl.enter_step("verify")
            step_start = _time.time()
            bus.emit("step_started", {
                "stage": "pre_review", "step": "verify",
                "message": "Running 3-check verification audit...",
            })

            verification_model = models.get("verification", "")
            verification = await verify_graph(
                paper_graph, document.text, self.llm,
                model=verification_model, session_id=session_id,
                markdown=document.markdown,
                domain_config=self.domain,
            )

            bus.emit("verify_complete", {
                "grounding_issues": len(verification.grounding_issues),
                "missing_concepts_added": verification.entities_added,
                "missing_connections": len(verification.missing_connections),
                "node_count": len(paper_graph.nodes),
                "edge_count": len(paper_graph.edges),
            })

            if verification.entities_added > 0 or verification.missing_connections:
                d3_data = paper_graph.to_d3_format()
                graph_cache[session_id] = d3_data
                bus.emit("graph_updated", {
                    "nodes": d3_data["nodes"], "edges": d3_data["edges"],
                    "node_count": len(d3_data["nodes"]),
                    "edge_count": len(d3_data["edges"]),
                })

            session = await self.sessions.get(session_id)
            if session:
                session.pipeline_steps["verify"] = StepState(
                    status="complete", started_at=step_start,
                    completed_at=_time.time(),
                    model_used=verification_model,
                    entities_flagged=verification.entities_flagged,
                ).model_dump()
                session.graph_after_step["verify"] = paper_graph.snapshot()
                self._write_checkpoint(session, "verification", "graph_after_step.verify")
                await self.sessions.update(session)
        else:
            logger.info("Skipping verification (checkpoint exists)")

        # ── Step 7: Summary ────────────────────────────────
        if not self._has_checkpoint(session, "summary"):
            ctl.enter_step("summarize")
            step_start = _time.time()
            bus.emit("step_started", {
                "stage": "pre_review", "step": "summarize",
                "message": "Generating graph summary for agents...",
            })

            bridged = paper_graph.ensure_structural_links()
            if bridged:
                logger.info("Created %d structural APPEARS_IN links", bridged)

            pruned = paper_graph.prune_ungrounded(threshold=pruning_threshold)
            if pruned:
                logger.info("Pruned %d ungrounded entities (confidence < %.2f)", pruned, pruning_threshold)

            paper_graph.summary = paper_graph.to_agent_briefing()
            paper_graph.update_stats()

            session = await self.sessions.get(session_id)
            if session:
                session.knowledge_graph = paper_graph.model_dump(mode="json")
                session.current_stage = "pre_review"
                session.pipeline_steps["summarize"] = StepState(
                    status="complete", started_at=step_start,
                    completed_at=_time.time(),
                    nodes_added=len(paper_graph.nodes),
                    edges_added=len(paper_graph.edges),
                ).model_dump()
                session.graph_after_step["summarize"] = paper_graph.snapshot()
                self._write_checkpoint(session, "summary", "knowledge_graph")
                await self.sessions.update(session)

                # Persist graph to disk for restart recovery
                try:
                    graph_dir = Path(self.sessions._storage_dir) / "graphs"
                    graph_dir.mkdir(parents=True, exist_ok=True)
                    graph_path = graph_dir / f"{session_id}_graph.json"
                    graph_path.write_text(json.dumps(paper_graph.to_d3_format(), indent=2))
                except Exception as e:
                    logger.warning("Failed to persist graph to disk for %s: %s", session_id, e)
        else:
            logger.info("Skipping summary (checkpoint exists)")

        ctl.stage_done("pre_review")
        bus.emit("stage_complete", {"stage": "pre_review"})

        return paper_graph
