"""
PC Panel API routes.

Endpoints: review, batch, preflight, conferences, graph, pipeline control,
export, ontology, and all PC-Panel-specific session operations.
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from protoneo.api.routes import (
    PipelineControl,
    SessionEventBus,
    get_engine,
    get_event_buses,
    get_llm_client,
    get_pipeline_controls,
    get_session_manager,
    _get_upload_dir,
)
from protoneo.config.schema import AgentConfig, DeliberationConfig
from protoneo.deliberation.session import SessionStatus, StepState
from protoneo.deliberation.types import DeliberationResult
from protoneo.knowledge.chunker import chunk_document
from protoneo.knowledge.graph_extractor import extract_paper_graph
from protoneo.knowledge.paper_graph import PaperGraph
from protoneo.knowledge.paper_ontology import generate_paper_ontology
from protoneo.knowledge.parser import parse_file

from .conference import ConferenceProfile, list_profiles, load_profile
from .export import packet_to_markdown, packet_to_pdf
from .pipeline import (
    _run_graph_pipeline,
    _run_pc_chair_review,
    _run_review_stage,
    get_session_graphs,
    get_session_ontologies,
)
from .preflight import run_preflight
from .review import (
    build_agent_configs,
    build_deliberation_config,
    build_user_message,
    result_to_packet,
)

logger = logging.getLogger("protoneo.pc_panel.api")


def register_pc_panel_routes(app: FastAPI) -> None:
    """Register all PC Panel routes on the FastAPI app."""

    _session_graphs = get_session_graphs()
    _session_ontologies = get_session_ontologies()

    # ── Conferences ────────────────────────────────────────

    @app.get("/api/conferences")
    async def get_conferences():
        profiles = list_profiles()
        return {
            "conferences": [
                {
                    "slug": p.slug,
                    "name": p.name,
                    "short_name": p.short_name,
                    "location": p.location,
                    "dates": p.dates,
                    "paper_types": p.paper_types,
                    "max_pages": p.max_pages,
                    "dual_anonymous": p.dual_anonymous,
                    "format_style": p.format_style,
                    "agent_count": len(p.panel_agents),
                    "optional_agent_count": len(p.optional_agents),
                    "scope_summary": p.scope_summary,
                }
                for p in profiles
            ]
        }

    @app.get("/api/conferences/{slug}")
    async def get_conference(slug: str):
        try:
            profile = load_profile(slug)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Conference '{slug}' not found")
        return profile.model_dump()

    # ── Preflight ────────────────────────────────────────

    @app.post("/api/panel/preflight")
    async def preflight_check(
        file: UploadFile = File(...),
        conference: str = Form("hpdc26"),
    ):
        """Run fast preflight checks on a manuscript before launching the full review."""
        try:
            profile = load_profile(conference)
        except FileNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"Conference profile '{conference}' not found"
            )

        upload_dir = _get_upload_dir()
        safe_name = f"{uuid.uuid4().hex}_{file.filename}"
        file_path = upload_dir / safe_name

        content = await file.read()
        file_path.write_bytes(content)

        try:
            doc = parse_file(str(file_path))
        except Exception as e:
            file_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {e}")
        finally:
            file_path.unlink(missing_ok=True)

        result = run_preflight(doc.text, doc.filename, profile)
        return result.model_dump(mode="json")

    # ── Review Sessions ────────────────────────────────────

    @app.post("/api/panel/review")
    async def start_panel_review(
        file: UploadFile = File(...),
        conference: str = Form("hpdc26"),
        model_map_json: str = Form("{}"),
        max_rounds: int = Form(2),
        user_instructions: str = Form(""),
    ):
        """Create and start a full PC Panel review session."""
        _session_manager = get_session_manager()
        _event_buses = get_event_buses()
        _pipeline_controls = get_pipeline_controls()

        try:
            profile = load_profile(conference)
        except FileNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"Conference profile '{conference}' not found"
            )

        try:
            model_map = json.loads(model_map_json) if model_map_json else {}
        except json.JSONDecodeError:
            model_map = {}

        upload_dir = _get_upload_dir()
        safe_name = f"{uuid.uuid4().hex}_{file.filename}"
        file_path = upload_dir / safe_name

        content = await file.read()
        file_path.write_bytes(content)

        try:
            doc = parse_file(str(file_path))
            from protoneo.knowledge.chunker import chunk_document
            doc = chunk_document(doc)
        except Exception as e:
            file_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {e}")

        agent_configs = build_agent_configs(
            profile=profile,
            conference_slug=conference,
            model_map=model_map if model_map else None,
            user_instructions=user_instructions,
        )

        reviewer_ids = [k for k in agent_configs if k != "meta"]
        delib_config = build_deliberation_config(
            reviewer_ids=reviewer_ids, max_rounds=max_rounds,
        )

        session = await _session_manager.create(
            config={
                "agents": {k: v.model_dump() for k, v in agent_configs.items()},
                "deliberation": delib_config.model_dump(),
                "metadata": {
                    "type": "panel_review",
                    "conference": conference,
                    "filename": file.filename,
                    "paper_title": "",
                },
            }
        )

        ctx = _session_manager.get_context(session.session_id)
        ctx.add_document(doc)
        session.document_ids.append(doc.document_id)
        await _session_manager.update(session)

        bus = SessionEventBus()
        _event_buses[session.session_id] = bus
        ctl = PipelineControl()
        _pipeline_controls[session.session_id] = ctl

        task = asyncio.create_task(_run_graph_pipeline(
            session.session_id, doc, profile, model_map,
            agent_configs, bus, ctl,
            delib_config=delib_config, graph_only=False,
        ))
        ctl.set_task(task)

        return {
            "session_id": session.session_id,
            "status": "running",
            "conference": conference,
            "filename": file.filename,
            "agents": list(agent_configs.keys()),
            "stages": PipelineControl.STAGES,
        }

    # ── Batch Upload ────────────────────────────────────

    @app.post("/api/panel/batch")
    async def start_batch(
        files: list[UploadFile] = File(...),
        conference: str = Form("hpdc26"),
        model_map_json: str = Form("{}"),
    ):
        """Upload N PDFs, create N sessions, build all graphs in parallel."""
        _session_manager = get_session_manager()
        _batch_manager = app.state.batch_manager
        _event_buses = get_event_buses()
        _pipeline_controls = get_pipeline_controls()

        try:
            profile = load_profile(conference)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Conference profile '{conference}' not found")

        try:
            model_map = json.loads(model_map_json) if model_map_json else {}
        except json.JSONDecodeError:
            model_map = {}

        agent_configs = build_agent_configs(
            profile=profile, conference_slug=conference,
            model_map=model_map if model_map else None,
        )

        upload_dir = _get_upload_dir()
        batch = await _batch_manager.create(conference=conference)

        session_ids = []
        for file in files:
            safe_name = f"{uuid.uuid4().hex}_{file.filename}"
            file_path = upload_dir / safe_name
            content = await file.read()
            file_path.write_bytes(content)

            try:
                doc = parse_file(str(file_path))
                doc = chunk_document(doc)
            except Exception as e:
                file_path.unlink(missing_ok=True)
                logger.warning("Failed to parse %s: %s", file.filename, e)
                continue

            session = await _session_manager.create(config={
                "agents": {k: v.model_dump() for k, v in agent_configs.items()},
                "metadata": {
                    "type": "panel_review",
                    "conference": conference,
                    "filename": file.filename,
                    "paper_title": "",
                },
            })
            session.batch_id = batch.batch_id
            await _session_manager.update(session)

            ctx = _session_manager.get_context(session.session_id)
            ctx.add_document(doc)
            session.document_ids.append(doc.document_id)
            await _session_manager.update(session)

            bus = SessionEventBus()
            _event_buses[session.session_id] = bus
            ctl = PipelineControl()
            _pipeline_controls[session.session_id] = ctl

            task = asyncio.create_task(_run_graph_pipeline(
                session.session_id, doc, profile, model_map,
                agent_configs, bus, ctl, graph_only=True,
            ))
            ctl.set_task(task)

            session_ids.append(session.session_id)

        batch.session_ids = session_ids
        await _batch_manager.update(batch)

        return {
            "batch_id": batch.batch_id,
            "session_count": len(session_ids),
            "session_ids": session_ids,
            "conference": conference,
            "status": "running",
        }

    @app.get("/api/panel/batch/{batch_id}")
    async def get_batch(batch_id: str):
        """Aggregated status of all sessions in a batch."""
        _session_manager = get_session_manager()
        _batch_manager = app.state.batch_manager

        batch = await _batch_manager.get(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        session_statuses = []
        completed = 0
        failed = 0
        for sid in batch.session_ids:
            session = await _session_manager.get(sid)
            if not session:
                session_statuses.append({"session_id": sid, "status": "unknown"})
                continue
            entry = {
                "session_id": sid,
                "status": session.status if isinstance(session.status, str) else session.status.value,
                "filename": session.config.get("metadata", {}).get("filename", ""),
                "paper_title": session.config.get("metadata", {}).get("paper_title", ""),
            }
            if session.paper_graph:
                try:
                    pg = PaperGraph.model_validate(session.paper_graph)
                    entry["node_count"] = len(pg.nodes)
                    entry["edge_count"] = len(pg.edges)
                except Exception:
                    pass
            session_statuses.append(entry)
            status_val = session.status if isinstance(session.status, str) else session.status.value
            if status_val == "completed":
                completed += 1
            elif status_val == "failed":
                failed += 1

        total = len(batch.session_ids)
        if completed == total:
            batch.status = "completed"
        elif failed == total:
            batch.status = "failed"
        elif completed + failed == total and failed > 0:
            batch.status = "partial"
        else:
            batch.status = "running"
        await _batch_manager.update(batch)

        return {
            "batch_id": batch.batch_id,
            "conference": batch.conference,
            "status": batch.status,
            "created_at": batch.created_at.isoformat(),
            "total": total,
            "completed": completed,
            "failed": failed,
            "sessions": session_statuses,
        }

    @app.get("/api/panel/batches")
    async def list_batches(limit: int = 20):
        """List recent batches."""
        _batch_manager = app.state.batch_manager
        batches = await _batch_manager.list_batches(limit=limit)
        return {
            "batches": [
                {
                    "batch_id": b.batch_id,
                    "conference": b.conference,
                    "status": b.status,
                    "session_count": len(b.session_ids),
                    "created_at": b.created_at.isoformat(),
                }
                for b in batches
            ]
        }

    # ── Launch Review on Existing Graph ─────────────────

    @app.post("/api/sessions/{session_id}/launch-review")
    async def launch_review(session_id: str):
        """Launch reviews on a session that already has a completed graph."""
        _session_manager = get_session_manager()
        _event_buses = get_event_buses()
        _pipeline_controls = get_pipeline_controls()

        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if not session.paper_graph:
            raise HTTPException(status_code=400, detail="Session has no graph. Build or import a graph first.")

        conference_slug = session.config.get("metadata", {}).get("conference", "hpdc26")
        try:
            profile = load_profile(conference_slug)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Conference profile '{conference_slug}' not found")

        agent_configs_raw = session.config.get("agents", {})
        agent_configs = {k: AgentConfig(**v) for k, v in agent_configs_raw.items()}
        delib_config = DeliberationConfig(**session.config.get("deliberation", {}))
        if not delib_config.max_rounds:
            reviewer_ids = [k for k in agent_configs if k != "meta"]
            delib_config = build_deliberation_config(reviewer_ids=reviewer_ids)

        pg = PaperGraph.model_validate(session.paper_graph)

        ctx = _session_manager.get_context(session_id)
        doc_text = session.paper_text or (ctx.documents[0].text if ctx.documents else "")

        class _MinimalDoc:
            def __init__(self, text, filename):
                self.text = text
                self.filename = filename
                self.chunks = []

        doc_proxy = _MinimalDoc(doc_text, session.config.get("metadata", {}).get("filename", "paper.pdf"))
        user_message = build_user_message(doc_proxy, profile)
        enriched_message = user_message + pg.summary

        bus = SessionEventBus()
        _event_buses[session_id] = bus
        ctl = PipelineControl()
        _pipeline_controls[session_id] = ctl

        session.status = SessionStatus.RUNNING
        await _session_manager.update(session)

        async def _run_review_only(sid: str) -> None:
            try:
                ctl.enter_stage("review")
                bus.emit("stage_started", {
                    "stage": "review", "step": "independent_reviews",
                    "message": "Starting peer review...",
                })

                ctl.enter_step("independent_reviews")
                bus.emit("step_started", {
                    "stage": "review", "step": "independent_reviews",
                    "message": "Starting independent peer reviews...",
                })

                result = await _run_review_stage(
                    sid, agent_configs, delib_config,
                    enriched_message, bus, ctl, pg,
                )

                await _run_pc_chair_review(sid, bus, ctl)

                sess = await _session_manager.get(sid)
                if sess:
                    sess.paper_graph = pg.model_dump(mode="json")
                    sess.current_stage = "review"
                    sess.status = SessionStatus.COMPLETED
                    await _session_manager.update(sess)

                _session_graphs[sid] = pg.to_d3_format()
                ctl.stage_done("review")
                bus.emit("stage_complete", {"stage": "review"})
                bus.emit("completed", {"result": sess.result if sess else {}})

            except asyncio.CancelledError:
                bus.emit("pipeline_cancelled", {"message": "Review cancelled"})
            except Exception as e:
                logger.error("Review failed for session %s: %s", sid, e, exc_info=True)
                bus.emit("error", {"detail": str(e)})
            finally:
                _pipeline_controls.pop(sid, None)

        task = asyncio.create_task(_run_review_only(session_id))
        ctl.set_task(task)

        return {
            "session_id": session_id,
            "status": "running",
            "stage": "review",
        }

    # ── Graph Export/Import ─────────────────────────────

    @app.get("/api/sessions/{session_id}/graph/export")
    async def export_graph(session_id: str):
        """Export the session's PaperGraph as a standalone JSON file."""
        _session_manager = get_session_manager()
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if not session.paper_graph:
            raise HTTPException(status_code=404, detail="No graph to export")

        export_data = {
            "schema_version": 1,
            "paper_title": session.config.get("metadata", {}).get("paper_title", ""),
            "conference": session.config.get("metadata", {}).get("conference", ""),
            "graph": session.paper_graph,
        }

        filename = f"graph-{session_id[:8]}.json"
        return Response(
            content=json.dumps(export_data, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/panel/review-with-graph")
    async def review_with_graph(
        graph_file: UploadFile = File(...),
        conference: str = Form("hpdc26"),
        model_map_json: str = Form("{}"),
        max_rounds: int = Form(2),
        user_instructions: str = Form(""),
    ):
        """Create a session with an imported graph and launch review immediately."""
        _session_manager = get_session_manager()
        _event_buses = get_event_buses()
        _pipeline_controls = get_pipeline_controls()

        try:
            profile = load_profile(conference)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Conference profile '{conference}' not found")

        graph_content = await graph_file.read()
        try:
            graph_data = json.loads(graph_content)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON in graph file")

        if "graph" in graph_data and "schema_version" in graph_data:
            paper_graph_dict = graph_data["graph"]
            paper_title = graph_data.get("paper_title", "")
        else:
            paper_graph_dict = graph_data
            paper_title = ""

        try:
            pg = PaperGraph.model_validate(paper_graph_dict)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid graph data: {e}")

        try:
            model_map = json.loads(model_map_json) if model_map_json else {}
        except json.JSONDecodeError:
            model_map = {}

        agent_configs = build_agent_configs(
            profile=profile, conference_slug=conference,
            model_map=model_map if model_map else None,
            user_instructions=user_instructions,
        )
        reviewer_ids = [k for k in agent_configs if k != "meta"]
        delib_config = build_deliberation_config(
            reviewer_ids=reviewer_ids, max_rounds=max_rounds,
        )

        session = await _session_manager.create(config={
            "agents": {k: v.model_dump() for k, v in agent_configs.items()},
            "deliberation": delib_config.model_dump(),
            "metadata": {
                "type": "panel_review",
                "conference": conference,
                "filename": graph_data.get("paper_title", "imported-graph"),
                "paper_title": paper_title,
            },
        })
        session.paper_graph = paper_graph_dict
        session.graph_source = "imported"
        if not pg.summary:
            pg.summary = pg.to_reviewer_summary()
            session.paper_graph = pg.model_dump(mode="json")
        await _session_manager.update(session)

        enriched_message = pg.summary

        bus = SessionEventBus()
        _event_buses[session.session_id] = bus
        ctl = PipelineControl()
        _pipeline_controls[session.session_id] = ctl

        session.status = SessionStatus.RUNNING
        await _session_manager.update(session)

        async def _run_imported_review(sid: str) -> None:
            try:
                ctl.enter_stage("review")
                bus.emit("stage_started", {
                    "stage": "review", "step": "independent_reviews",
                    "message": "Starting peer review with imported graph...",
                })
                ctl.enter_step("independent_reviews")
                bus.emit("step_started", {
                    "stage": "review", "step": "independent_reviews",
                    "message": "Starting independent peer reviews...",
                })

                result = await _run_review_stage(
                    sid, agent_configs, delib_config,
                    enriched_message, bus, ctl, pg,
                )
                await _run_pc_chair_review(sid, bus, ctl)

                sess = await _session_manager.get(sid)
                if sess:
                    sess.paper_graph = pg.model_dump(mode="json")
                    sess.current_stage = "review"
                    sess.status = SessionStatus.COMPLETED
                    await _session_manager.update(sess)

                _session_graphs[sid] = pg.to_d3_format()
                ctl.stage_done("review")
                bus.emit("stage_complete", {"stage": "review"})
                bus.emit("completed", {"result": sess.result if sess else {}})
            except asyncio.CancelledError:
                bus.emit("pipeline_cancelled", {"message": "Review cancelled"})
            except Exception as e:
                logger.error("Review failed for session %s: %s", sid, e, exc_info=True)
                bus.emit("error", {"detail": str(e)})
            finally:
                _pipeline_controls.pop(sid, None)

        task = asyncio.create_task(_run_imported_review(session.session_id))
        ctl.set_task(task)

        return {
            "session_id": session.session_id,
            "status": "running",
            "graph_source": "imported",
            "node_count": len(pg.nodes),
            "edge_count": len(pg.edges),
        }

    # ── Graph Utilization ──────────────────────────────

    @app.get("/api/sessions/{session_id}/graph-utilization")
    async def get_graph_utilization(session_id: str):
        """Compute how well reviewers utilized the knowledge graph."""
        _session_manager = get_session_manager()
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if not session.paper_graph:
            raise HTTPException(status_code=404, detail="No graph for this session")
        if not session.result:
            raise HTTPException(status_code=409, detail="Session has no review results yet")

        pg = PaperGraph.model_validate(session.paper_graph)

        reviews = []
        for phase in session.result.get("phases", []):
            if phase.get("phase_name") == "independent_review":
                for output in phase.get("outputs", []):
                    content = output.get("content", "")
                    parsed = {}
                    try:
                        parsed = json.loads(content) if content.strip().startswith("{") else {}
                    except (json.JSONDecodeError, ValueError):
                        pass
                    reviews.append({
                        "agent_id": output.get("agent_id", ""),
                        "reviewer_role": output.get("agent_role", ""),
                        "raw_content": content,
                        **parsed,
                    })

        utilization = pg.compute_utilization(reviews)
        return utilization

    # ── Review Packet ──────────────────────────────────

    @app.get("/api/sessions/{session_id}/review-packet")
    async def get_review_packet(session_id: str):
        """Get the structured review packet for a completed panel session."""
        _session_manager = get_session_manager()
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if session.status != SessionStatus.COMPLETED.value and session.status != "completed":
            raise HTTPException(
                status_code=409,
                detail=f"Session not yet completed (status: {session.status})",
            )

        if not session.result:
            raise HTTPException(status_code=404, detail="No result available")

        try:
            result = DeliberationResult.model_validate(session.result)
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to parse stored result: {e}"
            )

        conference_slug = session.config.get("metadata", {}).get("conference", "hpdc26")
        try:
            profile = load_profile(conference_slug)
        except FileNotFoundError:
            profile = ConferenceProfile(slug=conference_slug, name=conference_slug)

        paper_title = session.config.get("metadata", {}).get("paper_title", "")
        packet = result_to_packet(result, profile, paper_title)

        pc_chair_text = session.result.get("pc_chair_review", "")
        if pc_chair_text:
            packet.pc_chair_review = pc_chair_text

        if session.paper_graph:
            try:
                pg = PaperGraph.model_validate(session.paper_graph)
                packet.graph_summary = pg.summary
                packet.graph_node_count = len(pg.nodes)
                packet.graph_edge_count = len(pg.edges)

                if packet.reviews:
                    review_dicts = [r.model_dump() for r in packet.reviews]
                    packet.graph_utilization = pg.compute_utilization(review_dicts)
            except Exception:
                pass

        return packet.model_dump(mode="json")

    @app.get("/api/sessions/{session_id}/review-packet.md")
    async def get_review_packet_md(session_id: str):
        """Export the review packet as Markdown."""
        _session_manager = get_session_manager()
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if session.status != SessionStatus.COMPLETED.value and session.status != "completed":
            raise HTTPException(
                status_code=409,
                detail=f"Session not yet completed (status: {session.status})",
            )

        if not session.result:
            raise HTTPException(status_code=404, detail="No result available")

        try:
            result = DeliberationResult.model_validate(session.result)
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to parse stored result: {e}"
            )

        conference_slug = session.config.get("metadata", {}).get("conference", "hpdc26")
        try:
            profile = load_profile(conference_slug)
        except FileNotFoundError:
            profile = ConferenceProfile(slug=conference_slug, name=conference_slug)

        paper_title = session.config.get("metadata", {}).get("paper_title", "")
        packet = result_to_packet(result, profile, paper_title)
        md = packet_to_markdown(packet)

        return Response(
            content=md,
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="review-packet-{session_id}.md"'
            },
        )

    @app.get("/api/sessions/{session_id}/review-packet.pdf")
    async def get_review_packet_pdf(session_id: str):
        """Export the review packet as PDF."""
        _session_manager = get_session_manager()
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if session.status != SessionStatus.COMPLETED.value and session.status != "completed":
            raise HTTPException(
                status_code=409,
                detail=f"Session not yet completed (status: {session.status})",
            )

        if not session.result:
            raise HTTPException(status_code=404, detail="No result available")

        try:
            result = DeliberationResult.model_validate(session.result)
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to parse stored result: {e}"
            )

        conference_slug = session.config.get("metadata", {}).get("conference", "hpdc26")
        try:
            profile = load_profile(conference_slug)
        except FileNotFoundError:
            profile = ConferenceProfile(slug=conference_slug, name=conference_slug)

        paper_title = session.config.get("metadata", {}).get("paper_title", "")
        packet = result_to_packet(result, profile, paper_title)
        pdf_bytes = packet_to_pdf(packet)

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="review-packet-{session_id}.pdf"'
            },
        )

    # ── Paper Knowledge Graph ──────────────────────────

    @app.post("/api/sessions/{session_id}/generate-ontology")
    async def generate_ontology(
        session_id: str,
        model: str = Form(""),
    ):
        """Generate a paper-specific ontology before graph extraction."""
        _session_manager = get_session_manager()
        _llm_client = get_llm_client()
        _event_buses = get_event_buses()

        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        context = _session_manager.get_context(session_id)
        if not context.documents:
            raise HTTPException(status_code=400, detail="No document uploaded to this session")

        bus = _event_buses.get(session_id)
        if bus:
            bus.emit("graph_progress", {
                "phase": "ontology",
                "phase_num": 0,
                "total_phases": 3,
                "message": "Analyzing paper domain and designing review ontology...",
                "node_count": 0,
                "edge_count": 0,
            })

        conference_slug = session.config.get("metadata", {}).get("conference", "")
        conference_context = ""
        if conference_slug:
            try:
                profile = load_profile(conference_slug)
                conference_context = f"{profile.name}: {profile.scope_text()}"
            except FileNotFoundError:
                pass

        doc = context.documents[0]
        ontology = await generate_paper_ontology(
            doc.text, _llm_client, model=model,
            session_id=session_id, conference_context=conference_context,
        )

        _session_ontologies[session_id] = ontology

        if bus:
            bus.emit("graph_progress", {
                "phase": "ontology_complete",
                "phase_num": 0,
                "total_phases": 3,
                "message": f"Ontology ready: {len(ontology.entity_types)} entity types, {len(ontology.edge_types)} relationship types",
                "node_count": 0,
                "edge_count": 0,
            })

        return {
            "session_id": session_id,
            "entity_types": [et.model_dump() for et in ontology.entity_types],
            "edge_types": [rt.model_dump() for rt in ontology.edge_types],
            "analysis_summary": ontology.analysis_summary,
            "paper_domain": ontology.paper_domain,
            "key_contributions": ontology.key_contributions,
        }

    @app.get("/api/sessions/{session_id}/ontology")
    async def get_session_ontology(session_id: str):
        """Get the generated ontology for a session's paper."""
        ontology = _session_ontologies.get(session_id)
        if not ontology:
            raise HTTPException(status_code=404, detail="No ontology generated for this session")
        return {
            "entity_types": [et.model_dump() for et in ontology.entity_types],
            "edge_types": [rt.model_dump() for rt in ontology.edge_types],
            "analysis_summary": ontology.analysis_summary,
            "paper_domain": ontology.paper_domain,
            "key_contributions": ontology.key_contributions,
        }

    # ── Step-Level Pipeline Endpoints ─────────────────────

    @app.post("/api/sessions/{session_id}/pipeline/step/{step_name}/run")
    async def run_pipeline_step(session_id: str, step_name: str):
        """Run or re-run a single pipeline step."""
        _session_manager = get_session_manager()
        _event_buses = get_event_buses()

        valid_steps = ["nlp_prepass", "ontology", "extract", "coref", "verify", "summarize"]
        if step_name not in valid_steps:
            raise HTTPException(status_code=400, detail=f"Invalid step: {step_name}. Valid: {valid_steps}")

        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        bus = _event_buses.get(session_id)
        if not bus:
            bus = SessionEventBus()
            _event_buses[session_id] = bus

        step_idx = valid_steps.index(step_name)
        paper_graph = PaperGraph()
        if step_idx > 0:
            prev_step = valid_steps[step_idx - 1]
            prev_snapshot = session.graph_after_step.get(prev_step)
            if prev_snapshot:
                paper_graph = PaperGraph.restore_from_snapshot(prev_snapshot)
            elif session.paper_graph:
                paper_graph = PaperGraph.model_validate(session.paper_graph)

        import time as _time
        step_state = StepState(status="running", started_at=_time.time())
        session.pipeline_steps[step_name] = step_state.model_dump()

        for downstream in valid_steps[step_idx + 1:]:
            if downstream in session.pipeline_steps:
                ds = session.pipeline_steps[downstream]
                if isinstance(ds, dict):
                    ds["status"] = "pending"

        await _session_manager.update(session)

        bus.emit("step_started", {
            "stage": "pre_review", "step": step_name,
            "message": f"Running step: {step_name}",
        })

        return {
            "session_id": session_id,
            "step": step_name,
            "status": "running",
            "stale_steps": valid_steps[step_idx + 1:],
        }

    @app.post("/api/sessions/{session_id}/pipeline/step/{step_name}/cancel")
    async def cancel_pipeline_step(session_id: str, step_name: str):
        """Cancel a running pipeline step."""
        _session_manager = get_session_manager()
        _event_buses = get_event_buses()

        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if step_name in session.pipeline_steps:
            step_data = session.pipeline_steps[step_name]
            if isinstance(step_data, dict):
                step_data["status"] = "failed"
                step_data["error"] = "Cancelled by user"
                await _session_manager.update(session)

        bus = _event_buses.get(session_id)
        if bus:
            bus.emit("step_cancelled", {"step": step_name})

        return {"session_id": session_id, "step": step_name, "status": "cancelled"}

    @app.get("/api/sessions/{session_id}/pipeline/status")
    async def get_all_pipeline_steps(session_id: str):
        """Get all step states for a review session."""
        _session_manager = get_session_manager()
        _pipeline_controls = get_pipeline_controls()

        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        ctl = _pipeline_controls.get(session_id)
        return {
            "session_id": session_id,
            "pipeline_steps": session.pipeline_steps,
            "current_stage": session.current_stage,
            "active": ctl is not None,
            "pipeline_control": ctl.status() if ctl else None,
        }

    # ── Pipeline Control ────────────────────────────────

    @app.get("/api/sessions/{session_id}/pipeline")
    async def get_pipeline_status(session_id: str):
        """Get current pipeline state for a review session."""
        _pipeline_controls = get_pipeline_controls()
        ctl = _pipeline_controls.get(session_id)
        if not ctl:
            return {"session_id": session_id, "active": False}
        return {"session_id": session_id, "active": True, **ctl.status()}

    @app.post("/api/sessions/{session_id}/pipeline/advance")
    async def pipeline_advance(session_id: str):
        """Advance past the current gate (pre_review -> review)."""
        _pipeline_controls = get_pipeline_controls()
        _event_buses = get_event_buses()

        ctl = _pipeline_controls.get(session_id)
        if not ctl:
            raise HTTPException(status_code=404, detail="No active pipeline for this session")

        bus = _event_buses.get(session_id)
        if bus:
            bus.emit("pipeline_advanced", {
                "from_stage": ctl.current_stage,
                "message": f"Proceeding from {ctl.current_stage}",
            })

        ctl.advance()
        return {"session_id": session_id, **ctl.status()}

    @app.post("/api/sessions/{session_id}/pipeline/pause")
    async def pipeline_pause(session_id: str):
        """Pause the pipeline at its current position."""
        _pipeline_controls = get_pipeline_controls()
        _event_buses = get_event_buses()

        ctl = _pipeline_controls.get(session_id)
        if not ctl:
            raise HTTPException(status_code=404, detail="No active pipeline for this session")

        ctl.pause()
        bus = _event_buses.get(session_id)
        if bus:
            bus.emit("pipeline_paused", {
                "stage": ctl.current_stage,
                "step": ctl.current_step,
                "message": f"Pipeline paused at {ctl.current_stage}/{ctl.current_step}",
                **ctl.status(),
            })
        return {"session_id": session_id, **ctl.status()}

    @app.post("/api/sessions/{session_id}/pipeline/resume")
    async def pipeline_resume(session_id: str):
        """Resume pipeline in auto-advance mode."""
        _pipeline_controls = get_pipeline_controls()
        _event_buses = get_event_buses()

        ctl = _pipeline_controls.get(session_id)
        if not ctl:
            raise HTTPException(status_code=404, detail="No active pipeline for this session")

        ctl.resume()
        bus = _event_buses.get(session_id)
        if bus:
            bus.emit("pipeline_resumed", {
                "stage": ctl.current_stage,
                "message": "Pipeline resumed in auto-advance mode",
                **ctl.status(),
            })
        return {"session_id": session_id, **ctl.status()}

    @app.post("/api/sessions/{session_id}/pipeline/cancel")
    async def pipeline_cancel(session_id: str):
        """Cancel the entire review pipeline."""
        _session_manager = get_session_manager()
        _pipeline_controls = get_pipeline_controls()
        _event_buses = get_event_buses()

        ctl = _pipeline_controls.get(session_id)
        bus = _event_buses.get(session_id)

        if ctl:
            ctl.cancel()

        session = await _session_manager.get(session_id)
        if session:
            session.status = SessionStatus.STOPPED
            await _session_manager.update(session)

        if bus:
            bus.emit("pipeline_cancelled", {"message": "Review cancelled by PC chair"})
            bus.emit("error", {"detail": "Review cancelled by PC chair"})

        return {"session_id": session_id, "status": "cancelled"}

    class OntologyEdit(BaseModel):
        edited_entity_types: list[dict] | None = None
        edited_edge_types: list[dict] | None = None

    @app.post("/api/sessions/{session_id}/pipeline/edit-ontology")
    async def pipeline_edit_ontology(session_id: str, body: OntologyEdit):
        """Edit the generated ontology before advancing past it."""
        from protoneo.knowledge.paper_ontology import OntologyEntityType, OntologyEdgeType
        _event_buses = get_event_buses()

        ontology = _session_ontologies.get(session_id)
        if not ontology:
            raise HTTPException(status_code=404, detail="No ontology for this session")

        if body.edited_entity_types is not None:
            ontology.entity_types = [OntologyEntityType(**et) for et in body.edited_entity_types]
        if body.edited_edge_types is not None:
            ontology.edge_types = [OntologyEdgeType(**rt) for rt in body.edited_edge_types]
        _session_ontologies[session_id] = ontology

        bus = _event_buses.get(session_id)
        if bus:
            bus.emit("ontology_edited", {
                "entity_types": len(ontology.entity_types),
                "edge_types": len(ontology.edge_types),
                "message": "Ontology edited by PC chair",
            })
        return {"session_id": session_id, "entity_types": len(ontology.entity_types), "edge_types": len(ontology.edge_types)}

    @app.post("/api/sessions/{session_id}/extract-graph")
    async def extract_graph_endpoint(
        session_id: str,
        model: str = Form(""),
    ):
        """Extract a knowledge graph from the session's uploaded paper."""
        _session_manager = get_session_manager()
        _llm_client = get_llm_client()
        _event_buses = get_event_buses()

        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        context = _session_manager.get_context(session_id)
        if not context.documents:
            raise HTTPException(status_code=400, detail="No document uploaded to this session")

        bus = _event_buses.get(session_id)
        on_progress = None
        if bus:
            on_progress = lambda evt_type, data: bus.emit(evt_type, data)

        ontology = _session_ontologies.get(session_id)

        doc = context.documents[0]
        graph_data = await extract_paper_graph(
            doc.text, _llm_client, model=model,
            session_id=session_id, on_progress=on_progress,
            ontology=ontology,
        )
        _session_graphs[session_id] = graph_data

        return {
            "session_id": session_id,
            "node_count": len(graph_data.get("nodes", [])),
            "edge_count": len(graph_data.get("edges", [])),
        }

    @app.get("/api/sessions/{session_id}/graph")
    async def get_session_graph(session_id: str):
        """Get the knowledge graph for a session's paper."""
        _session_manager = get_session_manager()

        session = await _session_manager.get(session_id)
        if session and session.paper_graph:
            try:
                pg = PaperGraph.model_validate(session.paper_graph)
                d3 = pg.to_d3_format()
                d3["stats"] = pg.graph_stats()
                return d3
            except Exception:
                pass

        if session_id in _session_graphs:
            return _session_graphs[session_id]

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        context = _session_manager.get_context(session_id)
        if not context.documents:
            return {"nodes": [], "edges": []}

        from protoneo.knowledge.metadata import extract_metadata
        doc = context.documents[0]
        metadata = extract_metadata(doc.text)
        pg = PaperGraph()
        pg.ingest_metadata(metadata)
        return pg.to_d3_format()

    @app.get("/api/sessions/{session_id}/graph/step/{step_name}")
    async def get_graph_at_step(session_id: str, step_name: str):
        """Get the graph snapshot from after a specific pipeline step."""
        _session_manager = get_session_manager()
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        snapshot = session.graph_after_step.get(step_name)
        if not snapshot:
            raise HTTPException(status_code=404, detail=f"No graph snapshot for step '{step_name}'")

        try:
            pg = PaperGraph.restore_from_snapshot(snapshot)
            return {**pg.to_d3_format(), "stats": pg.graph_stats()}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to restore graph: {e}")

    @app.get("/api/sessions/{session_id}/reviewer-summary")
    async def get_reviewer_summary(session_id: str):
        """Get the reviewer summary text and graph stats."""
        _session_manager = get_session_manager()
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if not session.paper_graph:
            raise HTTPException(status_code=404, detail="No graph built yet")

        try:
            pg = PaperGraph.model_validate(session.paper_graph)
            return {
                "summary": pg.to_reviewer_summary(),
                "stats": pg.graph_stats(),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to generate summary: {e}")
