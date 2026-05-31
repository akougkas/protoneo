"""
Pipeline orchestration for Paper Review.

Contains the kernel graph pipeline invocation, review stage orchestration,
and unified final-review synthesis.
"""

import asyncio
import json
import logging
import re
from typing import Any

from protoneo.agents.types import Document
from protoneo.api.routes import (
    PipelineControl,
    SessionEventBus,
    get_engine,
    get_llm_client,
    get_session_manager,
    get_session_graphs,
    get_session_ontologies,
)
from protoneo.config.schema import AgentConfig, DeliberationConfig
from protoneo.deliberation.session import SessionStatus, StageCheckpoint
from protoneo.deliberation.types import DeliberationResult
from protoneo.knowledge.graph import KnowledgeGraph
from protoneo.llm.errors import sanitize_error_message
from .conference import ConferenceProfile
from .prompts import apply_output_guardrails, prompt_pack_no_chain_of_thought
from .review import (
    build_deliberation_config,
    build_user_message,
    parse_review_output,
    resolve_paper_review_model,
    strip_json_fences,
)
from .schemas import sanitize_final_review
from .web_context import build_review_web_context, review_web_search_enabled

logger = logging.getLogger("protoneo.paper_review.pipeline")

# Use kernel-level caches (shared with kernel routes)
_session_graphs = get_session_graphs()
_session_ontologies = get_session_ontologies()


def _build_review_graph_analysis(graph: KnowledgeGraph) -> str:
    """Generate structured graph analysis for reviewer context.

    Surfaces claim-evidence gaps, baseline coverage, section entity density,
    and disconnected entities that the narrative briefing does not capture.
    Reviewers can reference these findings when grounding their assessments.
    """
    if not graph.nodes:
        return (
            "\n\n## Structured Graph Analysis\n\n"
            "No knowledge graph entities are available for this session. "
            "Treat the manuscript text and inline figure/table annotations as the "
            "primary evidence source, and explicitly note that graph grounding is unavailable.\n"
        )

    _STRUCTURAL = {"Paper", "Section", "Diagram", "Table", "Reference", "Equation"}
    _STRUCTURAL_RELS = {"HAS_SECTION", "CONTAINS", "APPEARS_IN"}

    semantic = [n for n in graph.nodes if n.node_type not in _STRUCTURAL]
    if not semantic:
        return (
            "\n\n## Structured Graph Analysis\n\n"
            "The graph contains only structural paper nodes and no semantic claim, "
            "method, baseline, evidence, result, or dataset entities. Treat this as "
            "a graph coverage gap when evaluating unsupported claims.\n"
        )

    sem_edges = [e for e in graph.edges if e.edge_type not in _STRUCTURAL_RELS]

    # Build adjacency
    outgoing: dict[str, list[tuple[str, str]]] = {}
    incoming: dict[str, list[tuple[str, str]]] = {}
    for e in sem_edges:
        outgoing.setdefault(e.source_id, []).append((e.edge_type, e.target_id))
        incoming.setdefault(e.target_id, []).append((e.edge_type, e.source_id))

    typed: dict[str, list] = {}
    for n in semantic:
        typed.setdefault(n.node_type, []).append(n)

    lines = ["\n\n## Structured Graph Analysis\n"]

    # 1. Claim-Evidence Gaps
    claims = typed.get("Claim", [])
    if claims:
        _EVIDENCE_RELS = {"SUPPORTS", "EVIDENCED_BY", "EVALUATES_ON"}
        supported = []
        unsupported = []
        for c in claims:
            has_evidence = any(
                etype in _EVIDENCE_RELS for etype, _ in incoming.get(c.id, [])
            ) or any(
                etype in _EVIDENCE_RELS for etype, _ in outgoing.get(c.id, [])
            )
            (supported if has_evidence else unsupported).append(c)

        lines.append("### Claim-Evidence Coverage")
        lines.append(f"{len(supported)}/{len(claims)} claims have linked evidence.")
        if unsupported:
            lines.append("Claims without direct evidence links:")
            for c in unsupported[:5]:
                sec = f" [{c.source_section}]" if c.source_section else ""
                lines.append(f"- {c.label[:80]}{sec}")

    # 2. Baseline Coverage
    methods = typed.get("Method", [])
    baselines = typed.get("Baseline", [])
    if methods:
        compared = [
            m for m in methods
            if any(etype == "COMPARED_AGAINST" for etype, _ in outgoing.get(m.id, []))
        ]
        lines.append("\n### Baseline Coverage")
        lines.append(
            f"{len(compared)}/{len(methods)} methods have explicit baseline comparisons."
        )
        if baselines:
            names = [b.label.split(":")[0].strip() for b in baselines[:8]]
            lines.append(f"Known baselines: {', '.join(names)}")

    # 3. Section Entity Density
    section_counts: dict[str, int] = {}
    for n in semantic:
        if n.source_section:
            section_counts[n.source_section] = section_counts.get(n.source_section, 0) + 1

    if section_counts and graph.section_names:
        lines.append("\n### Section Coverage")
        covered_lower = {c.lower() for c in section_counts}
        empty = [s for s in graph.section_names if s.lower() not in covered_lower]
        if empty:
            lines.append(f"Sections with no extracted entities: {', '.join(empty[:5])}")
        for sec, count in sorted(section_counts.items(), key=lambda x: -x[1])[:6]:
            lines.append(f"- {sec}: {count} entities")

    # 4. Disconnected Entities
    connected_ids: set[str] = set()
    for e in sem_edges:
        connected_ids.add(e.source_id)
        connected_ids.add(e.target_id)
    orphans = [n for n in semantic if n.id not in connected_ids]
    if orphans:
        lines.append(f"\n### Disconnected Entities ({len(orphans)})")
        lines.append(
            "These entities were extracted but have no relationships to other entities:"
        )
        for o in orphans[:5]:
            lines.append(f"- {o.label} ({o.node_type})")

    result = "\n".join(lines) + "\n"
    if len(result) > 2000:
        result = result[:1800].rsplit("\n", 1)[0] + "\n"
    return result


def _build_enriched_review_message(user_message: str, graph: KnowledgeGraph) -> str:
    """Append graph summary and structured analysis for all reviewer agents."""
    parts = [user_message]
    if graph.summary:
        parts.append(graph.summary)
    else:
        parts.append(
            "\n\n## Knowledge Graph Summary\n\n"
            "No reviewer-facing graph summary is available for this session.\n"
        )
    parts.append(_build_review_graph_analysis(graph))
    return "\n\n".join(p.strip() for p in parts if p)


def _write_review_checkpoint(session, stage_name: str) -> None:
    """Write a checkpoint for a completed review stage."""
    from datetime import datetime, timezone
    if not any(cp.stage_name == stage_name for cp in session.checkpoints):
        session.checkpoints.append(StageCheckpoint(
            stage_name=stage_name,
            completed_at=datetime.now(timezone.utc).isoformat(),
            output_key="result",
        ))
        session.last_checkpoint = stage_name


def _session_conference_slug(session: Any | None) -> str:
    """Return the session's conference slug for prompt-pack guardrails."""
    if session is None:
        return ""
    config = getattr(session, "config", {}) or {}
    metadata = config.get("metadata", {}) if isinstance(config, dict) else {}
    conference = metadata.get("conference", "") if isinstance(metadata, dict) else ""
    return str(conference or "")


async def _run_review_stage(
    sid: str,
    agent_configs: dict[str, AgentConfig],
    delib_config: DeliberationConfig,
    enriched_message: str,
    bus: SessionEventBus,
    ctl: PipelineControl,
    paper_graph: KnowledgeGraph,
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
                bus.emit("step_completed", {
                    "stage": "review", "step": "independent_reviews",
                })
                ctl.enter_step("deliberation")
                bus.emit("step_started", {
                    "stage": "review", "step": "deliberation",
                    "message": "Starting reviewer deliberation...",
                })
            elif phase == "meta_review" and ctl.current_step != "meta_review":
                bus.emit("step_completed", {
                    "stage": "review", "step": "deliberation",
                })
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

    if review_web_search_enabled():
        bus.emit("web_search_started", {
            "stage": "review",
            "message": "Gathering external web search context...",
        })
        session = await _session_manager.get(sid)
        metadata = (session.config.get("metadata", {}) if session else {}) or {}
        fallback_title = str(metadata.get("paper_title") or metadata.get("filename") or "")
        web_context, web_metadata = await build_review_web_context(
            paper_graph,
            fallback_title=fallback_title,
        )
        if web_context:
            enriched_message = f"{enriched_message}\n\n{web_context}"
        web_metadata["markdown"] = web_context
        if session:
            if not hasattr(session, "app_data") or session.app_data is None:
                session.app_data = {}
            session.app_data["web_search"] = web_metadata
            await _session_manager.update(session)
        bus.emit("web_search_completed", {
            "stage": "review",
            "backend": web_metadata.get("backend", ""),
            "queries": web_metadata.get("queries", []),
            "result_count": web_metadata.get("result_count", 0),
            "enabled": web_metadata.get("enabled", False),
        })

    result = await _engine.run(
        session_id=sid,
        agent_configs=agent_configs,
        deliberation_config=delib_config,
        user_message=enriched_message,
        on_event=on_event,
    )

    # Emit step_completed for the final review step (meta_review)
    bus.emit("step_completed", {"stage": "review", "step": "meta_review"})

    # Store result and write per-phase review checkpoints
    session = await _session_manager.get(sid)
    if session:
        session.result = result.model_dump(mode="json")
        session.status = SessionStatus.RUNNING
        for phase in result.phases:
            _write_review_checkpoint(session, phase.phase_name)
        await _session_manager.update(session)

    # Annotate graph with review findings (derive roles from agent configs)
    role_keys = [k for k in agent_configs if k != "meta"]
    no_chain_of_thought = prompt_pack_no_chain_of_thought(
        _session_conference_slug(session)
    )
    for phase in result.phases:
        if phase.phase_name == "independent_review":
            for output in phase.outputs:
                role_guess = next(
                    (r for r in role_keys if r in output.agent_id), "unknown"
                )
                review = parse_review_output(
                    output,
                    role_guess,
                    no_chain_of_thought=no_chain_of_thought,
                )
                paper_graph.annotate_from_review(
                    review.model_dump(), agent_id=output.agent_id
                )

    await _finalize_unified_synthesis(sid, bus, result)

    return result


def _parse_final_review(raw: str) -> dict[str, Any]:
    """Extract structured final-review JSON from the unified synthesis output.

    Handles markdown code fences, leading/trailing text, and malformed JSON.
    Falls back to treating the raw output as comments_for_authors.
    """
    guarded_raw = apply_output_guardrails(raw, no_chain_of_thought=True)

    def _fallback() -> dict[str, Any]:
        return sanitize_final_review({}, fallback_comments=guarded_raw)

    def _normalize(parsed: dict[str, Any]) -> dict[str, Any]:
        return sanitize_final_review(parsed)

    cleaned = strip_json_fences(guarded_raw)

    # Try direct parse first
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return _normalize(parsed)
    except json.JSONDecodeError:
        pass

    # Try extracting the outermost { ... } block
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict):
                return _normalize(parsed)
        except json.JSONDecodeError:
            pass

    # Fallback: wrap raw text as comments_for_authors
    return _fallback()


async def _finalize_unified_synthesis(
    sid: str, bus: SessionEventBus, result: DeliberationResult,
) -> None:
    """Persist the meta-reviewer's single synthesis as the final review."""
    _session_manager = get_session_manager()

    output = result.final_output
    if not output:
        for phase in result.phases:
            if phase.phase_name == "meta_review" and phase.outputs:
                output = phase.outputs[0]
                break
    if not output:
        bus.emit("final_review_done", {"review": {}})
        return

    final_review = _parse_final_review(output.content)
    session = await _session_manager.get(sid)
    if session and session.result:
        conference_slug = session.config.get("metadata", {}).get("conference", "")
        session.result["final_review"] = final_review
        session.result["pc_chair_review"] = final_review
        if not hasattr(session, "app_data") or session.app_data is None:
            session.app_data = {}
        session.app_data["conference"] = conference_slug
        session.app_data["final_review"] = final_review
        session.app_data.pop("review_packet", None)
        await _session_manager.update(session)

    usage = output.metadata.get("usage", {}) if output.metadata else {}
    model = output.metadata.get("model", "") if output.metadata else ""
    dur = 0.0
    for phase in result.phases:
        if phase.phase_name == "meta_review":
            dur = round(phase.duration_seconds, 1)
            break

    payload = {
        "review": final_review,
        "model": model,
        "duration_seconds": dur,
        "tokens": usage.get("total_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "source_phase": "meta_review",
    }
    bus.emit("final_review_done", payload)
    # Backward-compatible event name consumed by existing UI clients.
    bus.emit("pc_chair_review_done", payload)


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
    skip_graph: bool = False,
) -> None:
    """Run the pre-review graph pipeline, optionally followed by review.

    When graph_only=True: runs the graph pipeline, sets session.status = "completed", returns.
    When graph_only=False: runs the graph pipeline, waits at gate, then runs review stage.
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
            session.document_text = doc.text
            session.document_markdown = doc.markdown or ""
            session.config["agents"] = {k: v.model_dump() for k, v in agent_configs.items()}
            if delib_config:
                session.config["deliberation"] = delib_config.model_dump()
            session.config.setdefault("metadata", {})["conference"] = profile.slug
            session.config["metadata"]["filename"] = doc.filename
            await _session_manager.update(session)

        paper_graph = KnowledgeGraph()

        # ════════════════════════════════════════════════
        # Skip-graph fast path: ingest metadata only
        # ════════════════════════════════════════════════
        if skip_graph:
            if doc.markdown:
                metadata = extract_metadata_from_markdown(doc.markdown, doc.text)
            else:
                metadata = extract_metadata(doc.text)
            paper_graph.ingest_metadata(metadata)
            paper_graph.summary = ""

            session = await _session_manager.get(sid)
            if session:
                if metadata.title:
                    session.config.setdefault("metadata", {})["paper_title"] = metadata.title
                session.knowledge_graph = paper_graph.model_dump(mode="json")
                await _session_manager.update(session)

            ctl.enter_stage("pre_review")
            ctl.stage_done("pre_review")
            bus.emit("stage_started", {
                "stage": "pre_review", "step": "parse",
                "message": "Skipping graph pipeline (A/B comparison mode)...",
            })
            bus.emit("stage_complete", {"stage": "pre_review"})

            if graph_only:
                session = await _session_manager.get(sid)
                if session:
                    session.status = SessionStatus.COMPLETED
                    await _session_manager.update(session)
                bus.emit("completed", {"result": {"graph_only": True, "skip_graph": True}})
                return

            # Proceed directly to review stage without gate
            ctl.enter_stage("review")
            bus.emit("stage_started", {
                "stage": "review", "step": "independent_reviews",
                "message": "Starting peer review (no graph enrichment)...",
            })

            user_message = build_user_message(doc, profile)
            enriched_message = _build_enriched_review_message(user_message, paper_graph)

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

            session = await _session_manager.get(sid)
            if session:
                session.knowledge_graph = paper_graph.model_dump(mode="json")
                session.current_stage = "review"
                session.status = SessionStatus.COMPLETED
                await _session_manager.update(session)

            _session_graphs[sid] = paper_graph.to_d3_format()

            ctl.stage_done("review")
            bus.emit("stage_complete", {"stage": "review"})
            bus.emit("completed", {"result": session.result if session else {}})
            return

        # ════════════════════════════════════════════════
        # Stage 1: Pre-Review (kernel graph pipeline)
        # ════════════════════════════════════════════════
        bus.emit("preflight_started", {
            "message": "Verifying model availability...",
        })

        def _resolve_graph_model(step_key: str) -> str:
            resolved = resolve_paper_review_model(
                step_key,
                model_map,
                fallback_keys=("ontology", "graph"),
                require_local=True,
                phase_policy="fast_structured",
            )
            if not resolved:
                logger.warning("No local model for graph step '%s'", step_key)
            return resolved

        resolved_models = {
            "ontology": _resolve_graph_model("ontology"),
            "extraction": _resolve_graph_model("extraction"),
            "coref": _resolve_graph_model("coref"),
            "verification": _resolve_graph_model("verification"),
        }

        logger.info(
            "Model map keys: %s | Resolved: %s",
            list(model_map.keys()), resolved_models,
        )

        # Read existing checkpoints so we can skip pinging models for completed steps
        session = await _session_manager.get(sid)
        completed_stages = set()
        if session and session.checkpoints:
            completed_stages = {cp.stage_name for cp in session.checkpoints}

        # Pre-flight: verify graph models are configured (all steps, even checkpointed)
        for step_name, model_id in resolved_models.items():
            if not model_id and step_name not in completed_stages:
                bus.emit("model_missing", {
                    "step": step_name,
                    "message": f"No model configured for graph step '{step_name}'",
                })
                raise RuntimeError(
                    f"No model configured for graph step '{step_name}'. "
                    "Configure a local endpoint (LM Studio, Ollama) in Settings."
                )

        # Build set of models that need pinging (only non-checkpointed steps)
        models_to_check: dict[str, str] = {}
        for step_name, model_id in resolved_models.items():
            if step_name in completed_stages:
                logger.info("Skipping pre-flight for '%s' (checkpoint exists)", step_name)
                continue
            if model_id and model_id not in models_to_check:
                models_to_check[model_id] = step_name

        _PREFLIGHT_TIMEOUT = 30.0
        for model_id, step_name in models_to_check.items():
            bus.emit("model_checking", {
                "model": model_id, "step": step_name,
                "message": f"Checking model '{model_id}'...",
            })
            try:
                await asyncio.wait_for(
                    _llm_client.complete(
                        model=model_id,
                        messages=[{"role": "user", "content": "ping"}],
                        max_tokens=1, temperature=0,
                        phase_policy="fast_structured",
                    ),
                    timeout=_PREFLIGHT_TIMEOUT,
                )
            except asyncio.TimeoutError:
                bus.emit("model_unreachable", {
                    "model": model_id, "step": step_name,
                    "error": f"No response within {_PREFLIGHT_TIMEOUT}s (including retries)",
                })
                raise RuntimeError(
                    f"Model '{model_id}' (used by {step_name}) did not respond within "
                    f"{_PREFLIGHT_TIMEOUT}s after retries. Check that the model server is running."
                )
            except Exception as ping_err:
                bus.emit("model_unreachable", {
                    "model": model_id, "step": step_name, "error": str(ping_err),
                })
                raise RuntimeError(
                    f"Model '{model_id}' (used by {step_name}) is unreachable: {ping_err}"
                ) from ping_err
        logger.info("Pre-flight model check passed for %d models (%d skipped via checkpoints)",
                     len(models_to_check), len(resolved_models) - len(models_to_check))

        bus.emit("stage_started", {
            "stage": "pre_review", "step": "parse",
            "message": "Starting pre-review analysis...",
        })

        # Run kernel graph pipeline
        from protoneo.knowledge.pipeline import GraphPipeline
        from .manifest import domain_config as _domain_config

        graph_pipeline = GraphPipeline(_llm_client, _session_manager, _domain_config)
        paper_graph = await graph_pipeline.run(
            session_id=sid,
            document=doc,
            bus=bus,
            ctl=ctl,
            models=resolved_models,
            pruning_threshold=profile.graph_pruning_threshold,
            conference_context=f"{profile.name}: {profile.scope_text()}",
            graph_cache=_session_graphs,
            ontology_cache=_session_ontologies,
        )

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
        enriched_message = _build_enriched_review_message(user_message, paper_graph)

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

        # Persist annotated graph
        session = await _session_manager.get(sid)
        if session:
            session.knowledge_graph = paper_graph.model_dump(mode="json")
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
        error = sanitize_error_message(e)
        logger.error("Pipeline failed for session %s: %s", sid, error, exc_info=True)
        session = await _session_manager.get(sid)
        if session:
            session.status = SessionStatus.FAILED
            session.error = error
            await _session_manager.update(session)
        bus.emit("error", {"detail": error})
    finally:
        _pipeline_controls.pop(sid, None)
        # Clean up per-session caches to avoid unbounded memory growth
        _session_ontologies.pop(sid, None)
