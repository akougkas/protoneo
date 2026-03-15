"""
Pipeline orchestration for PC Panel reviews.

Contains the main 7-step graph pipeline, review stage orchestration,
and PC Chair review generation.
"""

import asyncio
import logging
import time as _time
from typing import Any

from protoneo.agents.types import Document
from protoneo.api.routes import (
    PipelineControl,
    SessionEventBus,
    get_engine,
    get_llm_client,
    get_session_manager,
)
from protoneo.config.schema import AgentConfig, DeliberationConfig
from protoneo.deliberation.session import SessionStatus, StepState
from protoneo.deliberation.types import DeliberationResult
from protoneo.knowledge.chunker import chunk_document
from protoneo.knowledge.coref_resolver import resolve_coreferences
from protoneo.knowledge.graph_extractor import extract_paper_graph
from protoneo.knowledge.graph_verifier import verify_graph
from protoneo.knowledge.paper_graph import PaperGraph
from protoneo.knowledge.paper_ontology import generate_paper_ontology

from .conference import ConferenceProfile
from .review import (
    build_deliberation_config,
    build_user_message,
    parse_review_output,
)

logger = logging.getLogger("protoneo.pc_panel.pipeline")

# Per-session caches
_session_graphs: dict[str, dict] = {}
_session_ontologies: dict[str, Any] = {}


def get_session_graphs() -> dict[str, dict]:
    return _session_graphs


def get_session_ontologies() -> dict[str, Any]:
    return _session_ontologies


async def _run_review_stage(
    sid: str,
    agent_configs: dict[str, AgentConfig],
    delib_config: DeliberationConfig,
    enriched_message: str,
    bus: SessionEventBus,
    ctl: PipelineControl,
    paper_graph: PaperGraph,
) -> DeliberationResult:
    """Review stage: independent reviews -> deliberation -> meta-review.

    Wraps _engine.run() and intercepts phase events to emit
    step transitions for the 3-stage frontend.
    """
    _engine = get_engine()
    _session_manager = get_session_manager()
    _agent_buffers: dict[str, str] = {}

    def on_event(evt_type: str, data: dict) -> None:
        if evt_type == "phase_start":
            phase = data.get("phase", "")
            if phase == "deliberation" and ctl.current_step != "deliberation":
                ctl.enter_step("deliberation")
                bus.emit("step_started", {
                    "stage": "review", "step": "deliberation",
                    "message": "Starting reviewer deliberation...",
                })
            elif phase == "meta_review" and ctl.current_step != "meta_review":
                ctl.enter_step("meta_review")
                bus.emit("step_started", {
                    "stage": "review", "step": "meta_review",
                    "message": "Generating meta-review synthesis...",
                })

        if evt_type == "token" and ctl.current_step == "deliberation":
            aid = data.get("agent_id", "")
            _agent_buffers.setdefault(aid, "")
            _agent_buffers[aid] += data.get("chunk", "")

        if evt_type == "agent_start" and ctl.current_step == "deliberation":
            _agent_buffers[data.get("agent_id", "")] = ""

        if evt_type == "agent_done" and ctl.current_step == "deliberation":
            aid = data.get("agent_id", "")
            content = _agent_buffers.pop(aid, "")
            bus.emit("deliberation_turn", {
                "agent_id": aid,
                "role": data.get("role", ""),
                "round": data.get("round", 0),
                "content": content,
            })

        bus.emit(evt_type, data)

    result = await _engine.run(
        session_id=sid,
        agent_configs=agent_configs,
        deliberation_config=delib_config,
        user_message=enriched_message,
        on_event=on_event,
    )

    # Store result
    session = await _session_manager.get(sid)
    if session:
        session.result = result.model_dump(mode="json")
        session.status = SessionStatus.RUNNING
        await _session_manager.update(session)

    # Annotate graph with review findings (derive roles from agent configs)
    role_keys = [k for k in agent_configs if k != "meta"]
    for phase in result.phases:
        if phase.phase_name == "independent_review":
            for output in phase.outputs:
                role_guess = next(
                    (r for r in role_keys if r in output.agent_id), "unknown"
                )
                review = parse_review_output(output, role_guess)
                paper_graph.annotate_from_review(
                    review.model_dump(), agent_id=output.agent_id
                )

    return result


async def _run_pc_chair_review(
    sid: str, bus: SessionEventBus, ctl: PipelineControl,
) -> None:
    """PC Chair step: generate author-facing review letter."""
    _llm_client = get_llm_client()
    _session_manager = get_session_manager()

    ctl.enter_step("pc_chair")
    bus.emit("step_started", {
        "stage": "review", "step": "pc_chair",
        "message": "Generating PC Chair review for author feedback...",
    })

    session = await _session_manager.get(sid)
    if not session or not session.result:
        bus.emit("error", {"detail": "No deliberation result for PC Chair review"})
        return

    phases_data = session.result.get("phases", [])
    review_summaries = []
    for phase in phases_data:
        for output in phase.get("outputs", []):
            role = output.get("agent_role", "reviewer")
            content = output.get("content", "")
            if content:
                review_summaries.append(f"[{role}]:\n{content[:3000]}")

    if not review_summaries:
        bus.emit("pc_chair_review_done", {"review": ""})
        return

    pc_chair_prompt = (
        "You are the PC Chair writing the final author-facing review. "
        "Below are all reviewer assessments, deliberation exchanges, and "
        "the meta-review synthesis from the review committee.\n\n"
        "Write a polished, constructive review letter addressed to the authors. "
        "This review should:\n"
        "1. Summarize the committee's assessment of the paper\n"
        "2. List the key strengths identified by the committee\n"
        "3. Detail the main concerns and required revisions, prioritized by importance\n"
        "4. Provide specific, actionable feedback the authors can use\n"
        "5. State the overall recommendation (accept/revise/reject) with justification\n\n"
        "Write in a professional, encouraging tone. Be specific and constructive.\n"
        "Do NOT use JSON format. Write in plain prose with markdown headers.\n\n"
        + "\n\n---\n\n".join(review_summaries)
    )

    agent_cfgs = session.config.get("agents", {})
    meta_cfg = agent_cfgs.get("meta", {})
    chair_model = ""
    if isinstance(meta_cfg, dict):
        chair_model = meta_cfg.get("model", "")
    # Fall back to any reviewer model (never silently pick a cloud model)
    if not chair_model:
        for cfg in agent_cfgs.values():
            if isinstance(cfg, dict) and cfg.get("model"):
                chair_model = cfg["model"]
                break
    logger.info("PC Chair using model: %s", chair_model)

    bus.emit("agent_start", {
        "agent_id": "pc_chair", "role": "PC Chair", "model": chair_model,
    })

    chair_start = _time.monotonic()
    try:
        pc_chair_review = ""
        async for chunk in _llm_client.stream(
            model=chair_model,
            messages=[
                {"role": "system", "content": (
                    "You are the Program Committee Chair for an academic conference. "
                    "Write the final author-facing review letter that synthesizes "
                    "the committee's assessment into clear, actionable feedback."
                )},
                {"role": "user", "content": pc_chair_prompt},
            ],
            session_id=sid,
            temperature=0.3,
            max_tokens=8192,
        ):
            pc_chair_review += chunk
            bus.emit("token", {
                "agent_id": "pc_chair", "role": "PC Chair", "chunk": chunk,
            })

        dur = round(_time.monotonic() - chair_start, 1)
        bus.emit("agent_done", {
            "agent_id": "pc_chair", "role": "PC Chair",
            "model": chair_model, "duration_seconds": dur,
            "tokens": 0, "completion_tokens": 0,
        })

        session = await _session_manager.get(sid)
        if session and session.result:
            session.result["pc_chair_review"] = pc_chair_review
            await _session_manager.update(session)

        bus.emit("pc_chair_review_done", {
            "review": pc_chair_review, "model": chair_model,
            "duration_seconds": dur,
        })

    except Exception as e:
        logger.error("PC Chair review failed for session %s: %s", sid, e)
        dur = round(_time.monotonic() - chair_start, 1)
        bus.emit("agent_done", {
            "agent_id": "pc_chair", "role": "PC Chair",
            "model": chair_model, "duration_seconds": dur, "tokens": 0,
        })
        session = await _session_manager.get(sid)
        if session and session.result:
            session.result["pc_chair_review"] = f"PC Chair review generation failed: {e}"
            await _session_manager.update(session)


async def _run_graph_pipeline(
    sid: str,
    doc: Document,
    profile: ConferenceProfile,
    model_map: dict[str, str],
    agent_configs: dict[str, AgentConfig],
    bus: SessionEventBus,
    ctl: PipelineControl,
    delib_config: DeliberationConfig | None = None,
    graph_only: bool = False,
) -> None:
    """Run the pre-review graph pipeline (Steps 1-7), optionally followed by review.

    When graph_only=True: runs Steps 1-7, sets session.status = "completed", returns.
    When graph_only=False: runs Steps 1-7, waits at gate, then runs review stage.
    """
    from protoneo.knowledge.metadata import extract_metadata, extract_metadata_from_markdown
    from protoneo.api.routes import get_pipeline_controls

    _session_manager = get_session_manager()
    _llm_client = get_llm_client()
    _pipeline_controls = get_pipeline_controls()

    try:
        session = await _session_manager.get(sid)
        if session:
            session.status = SessionStatus.RUNNING
            await _session_manager.update(session)

        paper_graph = PaperGraph()

        # ════════════════════════════════════════════════
        # Stage 1: Pre-Review (7 steps, 1 graph)
        # ════════════════════════════════════════════════
        ctl.enter_stage("pre_review")
        bus.emit("stage_started", {
            "stage": "pre_review", "step": "parse",
            "message": "Starting pre-review analysis...",
        })

        # ── Step 1: Parse ──────────────────────────────
        ctl.enter_step("parse")
        step_start = _time.monotonic()
        bus.emit("step_started", {
            "stage": "pre_review", "step": "parse",
            "message": "PDF parsed, extracting structure...",
        })
        session = await _session_manager.get(sid)
        if session:
            session.pipeline_steps["parse"] = StepState(
                status="complete", started_at=step_start,
                completed_at=_time.monotonic(),
            ).model_dump()
            await _session_manager.update(session)

        # ── Step 2: NLP Pre-pass ───────────────────────
        ctl.enter_step("metadata")
        step_start = _time.monotonic()
        bus.emit("step_started", {
            "stage": "pre_review", "step": "metadata",
            "message": "Running NLP pre-pass: metadata, citations, equations...",
        })

        if doc.markdown:
            metadata = extract_metadata_from_markdown(doc.markdown, doc.text)
        else:
            metadata = extract_metadata(doc.text)
        paper_graph.ingest_metadata(metadata)

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
        _session_graphs[sid] = d3_data
        bus.emit("graph_updated", {
            "nodes": d3_data["nodes"],
            "edges": d3_data["edges"],
            "node_count": len(d3_data["nodes"]),
            "edge_count": len(d3_data["edges"]),
        })

        if metadata.title:
            session = await _session_manager.get(sid)
            if session:
                session.config.setdefault("metadata", {})["paper_title"] = metadata.title
                await _session_manager.update(session)

        session = await _session_manager.get(sid)
        if session:
            session.pipeline_steps["nlp_prepass"] = StepState(
                status="complete", started_at=step_start,
                completed_at=_time.monotonic(),
                nodes_added=len(paper_graph.nodes),
                edges_added=len(paper_graph.edges),
            ).model_dump()
            session.graph_after_step["nlp_prepass"] = paper_graph.snapshot()
            await _session_manager.update(session)

        # ── Step 3: Ontology ───────────────────────────
        ctl.enter_step("ontology")
        step_start = _time.monotonic()
        bus.emit("step_started", {
            "stage": "pre_review", "step": "ontology",
            "message": f"Generating review ontology for: {metadata.title[:80] if metadata.title else 'paper'}...",
        })

        conference_context = f"{profile.name}: {profile.scope_text()}"

        # Resolve per-step graph models from the model_map.
        # Falls back to first active model from settings, then agent configs.
        def _resolve_graph_model(step_key: str) -> str:
            m = model_map.get(step_key)
            if m:
                return m
            # Fall back to a shared graph model or first available
            m = model_map.get("ontology") or model_map.get("graph")
            if m:
                return m
            try:
                from protoneo.llm.settings import load_settings as _ls
                _active = (_ls().active_models or {})
                for _prov, _mid in _active.items():
                    if _mid:
                        return f"{_prov}/{_mid}"
            except Exception:
                pass
            return next(
                (c.model for c in agent_configs.values() if c.model), ""
            )

        ontology_model = _resolve_graph_model("ontology")
        extraction_model = _resolve_graph_model("extraction")
        coref_model = _resolve_graph_model("coref")
        verification_model = _resolve_graph_model("verification")

        logger.info(
            "Model map keys: %s | Resolved: ontology=%s extraction=%s coref=%s verify=%s",
            list(model_map.keys()), ontology_model, extraction_model, coref_model, verification_model,
        )

        ontology = await generate_paper_ontology(
            doc.text, _llm_client, model=ontology_model,
            session_id=sid, conference_context=conference_context,
            metadata=metadata, markdown=doc.markdown,
        )
        paper_graph.ontology = ontology
        paper_graph.add_ontology_nodes(ontology)
        _session_ontologies[sid] = ontology

        bus.emit("ontology_ready", {
            "entity_types": [et.model_dump() for et in ontology.entity_types],
            "edge_types": [rt.model_dump() for rt in ontology.edge_types],
            "paper_domain": ontology.paper_domain,
            "key_contributions": ontology.key_contributions,
            "analysis_summary": ontology.analysis_summary,
            "paused": not ctl.auto_advance,
        })

        session = await _session_manager.get(sid)
        if session:
            session.pipeline_steps["ontology"] = StepState(
                status="complete", started_at=step_start,
                completed_at=_time.monotonic(),
                model_used=ontology_model,
                nodes_added=len(paper_graph.nodes),
                edges_added=len(paper_graph.edges),
            ).model_dump()
            session.graph_after_step["ontology"] = paper_graph.snapshot()
            await _session_manager.update(session)

        await ctl.wait_if_paused()
        ontology = _session_ontologies[sid]
        paper_graph.ontology = ontology

        # ── Step 4: Section-Aware Extraction ───────────
        ctl.enter_step("extract")
        step_start = _time.monotonic()
        nodes_before = len(paper_graph.nodes)
        bus.emit("step_started", {
            "stage": "pre_review", "step": "extract",
            "message": f"Extracting knowledge graph ({len(ontology.entity_types)} entity types)...",
        })

        graph_data = await extract_paper_graph(
            doc.text, _llm_client, model=extraction_model,
            session_id=sid,
            on_progress=lambda evt, data: bus.emit(evt, data),
            ontology=ontology,
            paper_graph=paper_graph,
            markdown=doc.markdown,
        )

        _session_graphs[sid] = paper_graph.to_d3_format()

        bus.emit("graph_complete", {
            "node_count": len(paper_graph.nodes),
            "edge_count": len(paper_graph.edges),
        })

        session = await _session_manager.get(sid)
        if session:
            session.pipeline_steps["extract"] = StepState(
                status="complete", started_at=step_start,
                completed_at=_time.monotonic(),
                model_used=extraction_model,
                nodes_added=len(paper_graph.nodes) - nodes_before,
                edges_added=len(paper_graph.edges),
            ).model_dump()
            session.graph_after_step["extract"] = paper_graph.snapshot()
            await _session_manager.update(session)

        # ── Step 5: Co-reference Resolution ────────────
        ctl.enter_step("coref")
        step_start = _time.monotonic()
        bus.emit("step_started", {
            "stage": "pre_review", "step": "coref",
            "message": "Resolving co-references and linking abbreviations...",
        })

        coref_stats = await resolve_coreferences(
            paper_graph, _llm_client,
            model=coref_model, session_id=sid,
        )

        bus.emit("coref_complete", {
            "merged": coref_stats["merged"],
            "aliases_created": coref_stats["aliases_created"],
            "node_count": len(paper_graph.nodes),
            "edge_count": len(paper_graph.edges),
        })

        _session_graphs[sid] = paper_graph.to_d3_format()
        d3_data = paper_graph.to_d3_format()
        bus.emit("graph_updated", {
            "nodes": d3_data["nodes"],
            "edges": d3_data["edges"],
            "node_count": len(d3_data["nodes"]),
            "edge_count": len(d3_data["edges"]),
        })

        session = await _session_manager.get(sid)
        if session:
            session.pipeline_steps["coref"] = StepState(
                status="complete", started_at=step_start,
                completed_at=_time.monotonic(),
                model_used=coref_model,
            ).model_dump()
            session.graph_after_step["coref"] = paper_graph.snapshot()
            await _session_manager.update(session)

        # ── Step 6: Verification ───────────────────────
        ctl.enter_step("verify")
        step_start = _time.monotonic()
        bus.emit("step_started", {
            "stage": "pre_review", "step": "verify",
            "message": "Running 3-check verification audit...",
        })

        verification = await verify_graph(
            paper_graph, doc.text, _llm_client,
            model=verification_model, session_id=sid,
            markdown=doc.markdown,
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
            _session_graphs[sid] = d3_data
            bus.emit("graph_updated", {
                "nodes": d3_data["nodes"],
                "edges": d3_data["edges"],
                "node_count": len(d3_data["nodes"]),
                "edge_count": len(d3_data["edges"]),
            })

        session = await _session_manager.get(sid)
        if session:
            session.pipeline_steps["verify"] = StepState(
                status="complete", started_at=step_start,
                completed_at=_time.monotonic(),
                model_used=verification_model,
                entities_flagged=verification.entities_flagged,
            ).model_dump()
            session.graph_after_step["verify"] = paper_graph.snapshot()
            await _session_manager.update(session)

        # ── Step 7: Summarize ──────────────────────────
        ctl.enter_step("summarize")
        step_start = _time.monotonic()
        bus.emit("step_started", {
            "stage": "pre_review", "step": "summarize",
            "message": "Generating graph summary for reviewers...",
        })

        # Ensure all semantic nodes have APPEARS_IN edges to their sections
        bridged = paper_graph.ensure_structural_links()
        if bridged:
            logger.info("Created %d structural APPEARS_IN links", bridged)

        # Remove only low-confidence (likely hallucinated) entities, not orphans
        pruned = paper_graph.prune_ungrounded(threshold=0.3)
        if pruned:
            logger.info("Pruned %d ungrounded entities (confidence < 0.3)", pruned)
        paper_graph.summary = paper_graph.to_reviewer_summary()
        paper_graph.update_stats()

        session = await _session_manager.get(sid)
        if session:
            session.paper_graph = paper_graph.model_dump(mode="json")
            session.paper_text = doc.text
            session.current_stage = "pre_review"
            session.pipeline_steps["summarize"] = StepState(
                status="complete", started_at=step_start,
                completed_at=_time.monotonic(),
                nodes_added=len(paper_graph.nodes),
                edges_added=len(paper_graph.edges),
            ).model_dump()
            session.graph_after_step["summarize"] = paper_graph.snapshot()
            await _session_manager.update(session)

        ctl.stage_done("pre_review")
        bus.emit("stage_complete", {"stage": "pre_review"})

        # ═══════════════════════════════════════════════
        # Graph-only mode: stop here
        # ═══════════════════════════════════════════════
        if graph_only:
            session = await _session_manager.get(sid)
            if session:
                session.status = SessionStatus.COMPLETED
                await _session_manager.update(session)
            bus.emit("completed", {"result": {"graph_only": True}})
            return

        # ═══════════════════════════════════════════════
        # GATE: User inspects graph, clicks "Proceed to Review"
        # ═══════════════════════════════════════════════
        await ctl.wait_for_gate()

        # ════════════════════════════════════════════════
        # Stage 2: Review (automated after gate)
        # ════════════════════════════════════════════════
        ctl.enter_stage("review")
        bus.emit("stage_started", {
            "stage": "review", "step": "independent_reviews",
            "message": "Starting peer review...",
        })

        user_message = build_user_message(doc, profile)
        enriched_message = user_message + paper_graph.summary

        # ── Step 1: Independent Reviews ────────────────
        ctl.enter_step("independent_reviews")
        bus.emit("step_started", {
            "stage": "review", "step": "independent_reviews",
            "message": "Starting independent peer reviews...",
        })

        fallback_delib = delib_config or build_deliberation_config(
            reviewer_ids=[k for k in agent_configs if k != "meta"],
        )
        result = await _run_review_stage(
            sid, agent_configs, fallback_delib,
            enriched_message, bus, ctl, paper_graph,
        )

        # ── Step 4: PC Chair ───────────────────────────
        await _run_pc_chair_review(sid, bus, ctl)

        # Persist annotated graph
        session = await _session_manager.get(sid)
        if session:
            session.paper_graph = paper_graph.model_dump(mode="json")
            session.current_stage = "review"
            session.status = SessionStatus.COMPLETED
            await _session_manager.update(session)

        _session_graphs[sid] = paper_graph.to_d3_format()

        ctl.stage_done("review")
        bus.emit("stage_complete", {"stage": "review"})
        bus.emit("completed", {"result": session.result if session else {}})

    except asyncio.CancelledError:
        logger.info("Pipeline cancelled for session %s", sid)
        bus.emit("pipeline_cancelled", {"message": "Review cancelled"})
    except Exception as e:
        logger.error("Pipeline failed for session %s: %s", sid, e, exc_info=True)
        bus.emit("error", {"detail": str(e)})
    finally:
        _pipeline_controls.pop(sid, None)
