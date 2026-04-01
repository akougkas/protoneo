"""
Paper Review API routes.

Endpoints: review, batch, preflight, conferences, graph, pipeline control,
export, ontology, and all review-specific session operations.
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from protoneo.api.routes import (
    PipelineControl,
    SessionEventBus,
    get_batch_manager,
    get_event_buses,
    get_llm_client,
    get_pipeline_controls,
    get_session_manager,
    get_session_graphs,
    _get_upload_dir,
)
from protoneo.config.schema import AgentConfig, DeliberationConfig
from protoneo.deliberation.session import SessionStatus, StepState
from protoneo.deliberation.types import DeliberationResult
from protoneo.knowledge.chunker import chunk_document
from protoneo.knowledge.graph import KnowledgeGraph
from protoneo.knowledge.parser import parse_file

from .conference import ConferenceProfile, list_profiles, load_profile
from .manifest import domain_config as _domain_config

_APP_NAME = "paper_review"
_APP_VERSION = "0.1.0"
from .export import packet_to_markdown, packet_to_pdf
from .pipeline import (
    _run_graph_pipeline,
    _run_pc_chair_review,
    _run_review_stage,
)
from .preflight import run_preflight
from .prompts import load_prompt_pack
from .review import (
    build_agent_configs,
    build_deliberation_config,
    build_user_message,
    result_to_packet,
)

logger = logging.getLogger("protoneo.paper_review.api")

router = APIRouter()


def _extract_provenance_args(session) -> dict:
    """Extract agent_configs and prompt_pack_version from a stored session."""
    from protoneo.config.schema import AgentConfig as _AC
    agent_configs = {}
    for aid, cfg_dict in session.config.get("agents", {}).items():
        if isinstance(cfg_dict, dict):
            try:
                agent_configs[aid] = _AC.model_validate(cfg_dict)
            except Exception:
                pass

    conference_slug = session.config.get("metadata", {}).get("conference", "hpdc26")
    prompt_pack_version = ""
    try:
        pack = load_prompt_pack(conference_slug)
        prompt_pack_version = pack.get("version", "")
    except Exception:
        pass

    return {"agent_configs": agent_configs, "prompt_pack_version": prompt_pack_version}


class _MinimalDoc:
    """Lightweight document proxy for build_user_message() when
    the full Document model is unavailable (e.g., launch-review
    on a session that only has persisted text)."""

    def __init__(self, text: str, markdown: str, filename: str):
        self.text = text
        self.markdown = markdown
        self.filename = filename
        self.chunks: list[str] = []


def _is_completed(session) -> bool:
    """Check if a session has completed, handling both enum and string status."""
    return session.status in (SessionStatus.COMPLETED, SessionStatus.COMPLETED.value)


async def _recover_stale_sessions() -> None:
    """Mark sessions stuck in 'running' as 'stopped' on startup.

    When the backend restarts mid-pipeline, in-memory state
    (PipelineControl, SessionEventBus) is lost. These sessions can
    never resume, so mark them stopped to unblock the UI.

    Also reload persisted graph data into _session_graphs so graph
    visualization works after restart.
    """
    _session_manager = get_session_manager()
    _session_graphs = get_session_graphs()
    sessions = await _session_manager.list_sessions(limit=100)
    recovered = 0
    graphs_loaded = 0
    for s in sessions:
        status_val = s.status if isinstance(s.status, str) else s.status.value
        if status_val == "running":
            s.status = SessionStatus.STOPPED
            await _session_manager.update(s)
            recovered += 1
            logger.info("Recovered stale session %s (was running, now stopped)", s.session_id)

        # Reload graph from session data into in-memory cache
        if s.knowledge_graph and s.session_id not in _session_graphs:
            try:
                pg = KnowledgeGraph.model_validate(s.knowledge_graph)
                _session_graphs[s.session_id] = pg.to_d3_format()
                graphs_loaded += 1
            except Exception:
                pass

    if recovered:
        logger.info("Recovered %d stale sessions on startup", recovered)
    if graphs_loaded:
        logger.info("Reloaded %d graphs into memory on startup", graphs_loaded)


# ── Conferences ────────────────────────────────────────

@router.get("/conferences")
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

@router.get("/conferences/{slug}")
async def get_conference(slug: str):
    try:
        profile = load_profile(slug)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Conference '{slug}' not found")
    return profile.model_dump()

# ── Preflight ────────────────────────────────────────

@router.post("/preflight")
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
        doc = parse_file(str(file_path), fast=True)
    except Exception as e:
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Failed to parse PDF: {e}")
    finally:
        file_path.unlink(missing_ok=True)

    result = run_preflight(doc.text, doc.filename, profile)
    return result.model_dump(mode="json")

# ── Review Sessions ────────────────────────────────────

@router.post("/review")
async def start_panel_review(
    file: UploadFile = File(...),
    conference: str = Form("hpdc26"),
    model_map_json: str = Form("{}"),
    max_rounds: int = Form(2),
    user_instructions: str = Form(""),
    skip_graph: bool = Form(False),
    fast_parse: bool = Form(False),
):
    """Create and start a full Paper Review session.

    Returns immediately with session_id. PDF parsing (pdf2md) runs
    in the background so the UI can navigate to the session page.
    pdf2md uses local AI (Nemotron + VLM) for clean markdown extraction.
    """
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
        },
        app_name=_APP_NAME,
        app_version=_APP_VERSION,
    )

    bus = SessionEventBus()
    _event_buses[session.session_id] = bus
    ctl = PipelineControl()
    _pipeline_controls[session.session_id] = ctl

    async def _parse_and_run(sid: str) -> None:
        """Parse PDF in background, then run the full pipeline."""
        import time as _time

        session = await _session_manager.get(sid)
        if session:
            session.status = SessionStatus.RUNNING
            session.pipeline_steps["parse"] = StepState(
                status="running", started_at=_time.monotonic(),
            ).model_dump()
            await _session_manager.update(session)

        bus.emit("step_started", {
            "stage": "pre_review", "step": "parse",
            "message": f"Parsing {file.filename}...",
        })
        await asyncio.sleep(0)

        try:
            loop = asyncio.get_running_loop()
            doc = await loop.run_in_executor(
                None, parse_file, str(file_path), fast_parse,
            )
            doc = chunk_document(doc)
        except Exception as e:
            file_path.unlink(missing_ok=True)
            logger.warning("Failed to parse %s: %s", file.filename, e)
            session = await _session_manager.get(sid)
            if session:
                session.status = SessionStatus.FAILED
                session.error = f"Parse failed: {e}"
                await _session_manager.update(session)
            bus.emit("error", {"detail": f"Parse failed: {e}"})
            return

        ctx = _session_manager.get_context(sid)
        ctx.add_document(doc)
        session = await _session_manager.get(sid)
        if session:
            session.document_ids.append(doc.document_id)
            await _session_manager.update(session)

        await _run_graph_pipeline(
            sid, doc, profile, model_map,
            agent_configs, bus, ctl,
            delib_config=delib_config, graph_only=False,
            skip_graph=skip_graph,
        )

    task = asyncio.create_task(_parse_and_run(session.session_id))
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

@router.post("/batch")
async def start_batch(
    files: list[UploadFile] = File(...),
    conference: str = Form("hpdc26"),
    model_map_json: str = Form("{}"),
    fast_parse: bool = Form(True),
):
    """Upload N PDFs, create N sessions, build all graphs in parallel.

    Returns immediately with batch_id. Parsing and pipeline work
    runs in the background so the frontend can redirect instantly.
    """
    _session_manager = get_session_manager()
    _batch_manager = get_batch_manager()
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

    # Save files to disk and create sessions immediately (no parsing yet)
    pending: list[tuple[str, Path, str]] = []  # (session_id, file_path, filename)
    session_ids = []
    for file in files:
        safe_name = f"{uuid.uuid4().hex}_{file.filename}"
        file_path = upload_dir / safe_name
        content = await file.read()
        file_path.write_bytes(content)

        session = await _session_manager.create(
            config={
                "agents": {k: v.model_dump() for k, v in agent_configs.items()},
                "metadata": {
                    "type": "panel_review",
                    "conference": conference,
                    "filename": file.filename,
                    "paper_title": "",
                },
            },
            app_name=_APP_NAME,
            app_version=_APP_VERSION,
        )
        session.batch_id = batch.batch_id
        await _session_manager.update(session)
        session_ids.append(session.session_id)
        pending.append((session.session_id, file_path, file.filename))

    batch.session_ids = session_ids
    await _batch_manager.update(batch)

    # Process papers sequentially in a single background task.
    # Local LLMs cannot handle parallel pipelines (each pipeline
    # already runs 4-way extraction internally).
    batch_bus = SessionEventBus()
    _event_buses[f"batch_{batch.batch_id}"] = batch_bus

    async def _run_batch_sequential() -> None:
        import time as _time
        for i, (sid, fpath, fname) in enumerate(pending):
            bus = _event_buses.get(sid)
            if not bus:
                bus = SessionEventBus()
                _event_buses[sid] = bus

            batch_bus.emit("batch_progress", {
                "current": i + 1, "total": len(pending),
                "filename": fname, "session_id": sid,
            })

            session = await _session_manager.get(sid)
            if session:
                session.status = SessionStatus.RUNNING
                session.pipeline_steps["parse"] = StepState(
                    status="running", started_at=_time.monotonic(),
                ).model_dump()
                await _session_manager.update(session)

            bus.emit("step_started", {
                "stage": "pre_review", "step": "parse",
                "message": f"Parsing {fname} ({i + 1}/{len(pending)})...",
            })
            await asyncio.sleep(0)

            try:
                loop = asyncio.get_running_loop()
                doc = await loop.run_in_executor(
                    None, parse_file, str(fpath), fast_parse,
                )
                doc = chunk_document(doc)
            except Exception as e:
                fpath.unlink(missing_ok=True)
                logger.warning("Failed to parse %s: %s", fname, e)
                session = await _session_manager.get(sid)
                if session:
                    session.status = SessionStatus.FAILED
                    session.error = f"Parse failed: {e}"
                    await _session_manager.update(session)
                bus.emit("error", {"detail": f"Parse failed: {e}"})
                batch_bus.emit("paper_failed", {
                    "session_id": sid, "error": f"Parse failed: {e}",
                })
                continue

            session = await _session_manager.get(sid)
            if session:
                ctx = _session_manager.get_context(sid)
                ctx.add_document(doc)
                session.document_ids.append(doc.document_id)
                await _session_manager.update(session)

            ctl = PipelineControl()
            _pipeline_controls[sid] = ctl

            try:
                await _run_graph_pipeline(
                    sid, doc, profile, model_map,
                    agent_configs, bus, ctl, graph_only=True,
                )
                batch_bus.emit("paper_complete", {
                    "session_id": sid, "index": i + 1,
                    "total": len(pending), "filename": fname,
                })
            except Exception as e:
                logger.error("Pipeline failed for %s in batch: %s", fname, e)
                session = await _session_manager.get(sid)
                if session and session.status != SessionStatus.FAILED:
                    session.status = SessionStatus.FAILED
                    session.error = str(e)
                    await _session_manager.update(session)
                batch_bus.emit("paper_failed", {
                    "session_id": sid, "error": str(e),
                })

        batch_bus.emit("batch_complete", {"total": len(pending)})

    asyncio.create_task(_run_batch_sequential())

    return {
        "batch_id": batch.batch_id,
        "session_count": len(session_ids),
        "session_ids": session_ids,
        "conference": conference,
        "status": "running",
    }

@router.get("/batch/{batch_id}")
async def get_batch(batch_id: str):
    """Aggregated status of all sessions in a batch."""
    _session_manager = get_session_manager()
    _batch_manager = get_batch_manager()

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
            "pipeline_steps": session.pipeline_steps or {},
            "current_stage": session.current_stage or "",
        }
        if session.knowledge_graph:
            try:
                pg = KnowledgeGraph.model_validate(session.knowledge_graph)
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
    stopped = sum(
        1 for s in session_statuses
        if s.get("status") in ("stopped", "unknown")
    )
    terminal = completed + failed + stopped
    if completed == total:
        batch.status = "completed"
    elif failed == total:
        batch.status = "failed"
    elif terminal == total and failed > 0:
        batch.status = "partial"
    elif terminal == total and stopped > 0 and completed == 0:
        batch.status = "stopped"
    elif terminal == total:
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

@router.get("/batches")
async def list_batches(limit: int = 20):
    """List recent batches."""
    _batch_manager = get_batch_manager()
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

# ── Batch Review (Sequential Full Pipeline) ──────────

@router.post("/batch-review")
async def start_batch_review(
    files: list[UploadFile] = File(...),
    conference: str = Form("hpdc26"),
    model_map_json: str = Form("{}"),
    max_rounds: int = Form(2),
    user_instructions: str = Form(""),
    fast_parse: bool = Form(False),
):
    """Upload N PDFs, process each sequentially through the full pipeline.

    Unlike /api/panel/batch (graph-only), this runs graph + review for each
    paper with auto-advancing gates. Returns immediately with batch_id.
    """
    _session_manager = get_session_manager()
    _batch_manager = get_batch_manager()
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
        user_instructions=user_instructions,
    )

    upload_dir = _get_upload_dir()
    batch = await _batch_manager.create(conference=conference)

    pending: list[tuple[str, Path, str]] = []
    session_ids = []
    for file in files:
        safe_name = f"{uuid.uuid4().hex}_{file.filename}"
        file_path = upload_dir / safe_name
        content = await file.read()
        file_path.write_bytes(content)

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
            },
            app_name=_APP_NAME,
            app_version=_APP_VERSION,
        )
        session.batch_id = batch.batch_id
        await _session_manager.update(session)
        session_ids.append(session.session_id)
        pending.append((session.session_id, file_path, file.filename))

    batch.session_ids = session_ids
    await _batch_manager.update(batch)

    batch_bus = SessionEventBus()
    _event_buses[f"batch_{batch.batch_id}"] = batch_bus

    async def _run_batch_review_sequential() -> None:
        import time as _time
        for i, (sid, fpath, fname) in enumerate(pending):
            bus = _event_buses.get(sid)
            if not bus:
                bus = SessionEventBus()
                _event_buses[sid] = bus

            batch_bus.emit("batch_progress", {
                "current": i + 1, "total": len(pending),
                "filename": fname, "session_id": sid,
            })

            session = await _session_manager.get(sid)
            if session:
                session.status = SessionStatus.RUNNING
                session.pipeline_steps["parse"] = StepState(
                    status="running", started_at=_time.monotonic(),
                ).model_dump()
                await _session_manager.update(session)

            bus.emit("step_started", {
                "stage": "pre_review", "step": "parse",
                "message": f"Parsing {fname} ({i + 1}/{len(pending)})...",
            })
            await asyncio.sleep(0)

            try:
                loop = asyncio.get_running_loop()
                doc = await loop.run_in_executor(
                    None, parse_file, str(fpath), fast_parse,
                )
                doc = chunk_document(doc)
            except Exception as e:
                fpath.unlink(missing_ok=True)
                logger.warning("Failed to parse %s: %s", fname, e)
                session = await _session_manager.get(sid)
                if session:
                    session.status = SessionStatus.FAILED
                    session.error = f"Parse failed: {e}"
                    await _session_manager.update(session)
                bus.emit("error", {"detail": f"Parse failed: {e}"})
                batch_bus.emit("paper_failed", {
                    "session_id": sid, "error": f"Parse failed: {e}",
                })
                continue

            session = await _session_manager.get(sid)
            if session:
                ctx = _session_manager.get_context(sid)
                ctx.add_document(doc)
                session.document_ids.append(doc.document_id)
                await _session_manager.update(session)

            ctl = PipelineControl()
            ctl.skip_gate = True
            _pipeline_controls[sid] = ctl

            reviewer_ids = [k for k in agent_configs if k != "meta"]
            delib_config = build_deliberation_config(
                reviewer_ids=reviewer_ids, max_rounds=max_rounds,
            )

            try:
                # Run full pipeline (graph + review) with auto-advance gate
                await _run_graph_pipeline(
                    sid, doc, profile, model_map,
                    agent_configs, bus, ctl,
                    delib_config=delib_config,
                    graph_only=False,
                )
                batch_bus.emit("paper_complete", {
                    "session_id": sid, "index": i + 1,
                    "total": len(pending), "filename": fname,
                })
            except Exception as e:
                logger.error("Full pipeline failed for %s in batch: %s", fname, e)
                session = await _session_manager.get(sid)
                if session and session.status != SessionStatus.FAILED:
                    session.status = SessionStatus.FAILED
                    session.error = str(e)
                    await _session_manager.update(session)
                batch_bus.emit("paper_failed", {
                    "session_id": sid, "error": str(e),
                })

        batch_bus.emit("batch_complete", {"total": len(pending)})

    asyncio.create_task(_run_batch_review_sequential())

    return {
        "batch_id": batch.batch_id,
        "session_count": len(session_ids),
        "session_ids": session_ids,
        "conference": conference,
        "status": "running",
        "mode": "full_review",
    }

# ── Retry Endpoints ───────────────────────────────────

@router.post("/sessions/{session_id}/retry")
async def retry_session(session_id: str):
    """Retry a failed or stopped session from the last completed step."""
    _session_manager = get_session_manager()
    _event_buses = get_event_buses()
    _pipeline_controls = get_pipeline_controls()

    session = await _session_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    status_val = session.status if isinstance(session.status, str) else session.status.value
    if status_val not in ("failed", "stopped"):
        raise HTTPException(
            status_code=409,
            detail=f"Session is '{status_val}', only failed or stopped sessions can be retried",
        )

    conference_slug = session.config.get("metadata", {}).get("conference", "hpdc26")
    try:
        profile = load_profile(conference_slug)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Conference '{conference_slug}' not found")

    agent_configs_raw = session.config.get("agents", {})
    agent_configs = {k: AgentConfig(**v) for k, v in agent_configs_raw.items()}
    delib_config = DeliberationConfig(**session.config.get("deliberation", {}))

    # Reconstruct document from persisted data
    doc_text = session.document_text
    doc_md = session.document_markdown or ""
    filename = session.config.get("metadata", {}).get("filename", "paper.pdf")
    if not doc_text:
        raise HTTPException(status_code=400, detail="No paper text stored. Cannot retry.")

    from protoneo.agents.types import Document as _Doc
    doc = _Doc(
        document_id=uuid.uuid4().hex,
        filename=filename,
        text=doc_text,
        markdown=doc_md,
    )

    model_map_raw = {}
    for step_key in ("ontology", "extraction", "coref", "verification"):
        for step_data in session.pipeline_steps.values():
            if isinstance(step_data, dict) and step_data.get("model_used"):
                model_map_raw.setdefault(step_key, step_data["model_used"])
    for k, v in agent_configs_raw.items():
        if isinstance(v, dict) and v.get("model"):
            model_map_raw[k] = v["model"]

    session.status = SessionStatus.RUNNING
    session.error = None
    await _session_manager.update(session)

    bus = SessionEventBus()
    _event_buses[session_id] = bus
    ctl = PipelineControl()
    _pipeline_controls[session_id] = ctl

    # Determine if graph pipeline already completed (checkpoint-based, not stage-based).
    # If the summary checkpoint exists, graph is done; retry runs review stages.
    graph_complete = any(cp.stage_name == "summary" for cp in session.checkpoints)
    task = asyncio.create_task(_run_graph_pipeline(
        session_id, doc, profile, model_map_raw,
        agent_configs, bus, ctl,
        delib_config=delib_config,
        graph_only=(not graph_complete),
    ))
    ctl.set_task(task)

    return {"session_id": session_id, "status": "running", "action": "retry"}

@router.post("/batch/{batch_id}/retry-failed")
async def retry_failed_in_batch(batch_id: str):
    """Retry all failed sessions in a batch sequentially."""
    _session_manager = get_session_manager()
    _batch_manager = get_batch_manager()
    _event_buses = get_event_buses()
    _pipeline_controls = get_pipeline_controls()

    batch = await _batch_manager.get(batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    failed_sids = []
    for sid in batch.session_ids:
        session = await _session_manager.get(sid)
        if session:
            status_val = session.status if isinstance(session.status, str) else session.status.value
            if status_val in ("failed", "stopped"):
                failed_sids.append(sid)

    if not failed_sids:
        return {"batch_id": batch_id, "retried": 0, "message": "No failed sessions to retry"}

    batch_bus = SessionEventBus()
    _event_buses[f"batch_{batch_id}"] = batch_bus

    async def _retry_failed_sequential() -> None:
        for i, sid in enumerate(failed_sids):
            batch_bus.emit("batch_progress", {
                "current": i + 1, "total": len(failed_sids),
                "session_id": sid, "action": "retry",
            })

            session = await _session_manager.get(sid)
            if not session:
                continue

            conference_slug = session.config.get("metadata", {}).get("conference", "hpdc26")
            try:
                profile = load_profile(conference_slug)
            except FileNotFoundError:
                continue

            agent_configs_raw = session.config.get("agents", {})
            ac = {k: AgentConfig(**v) for k, v in agent_configs_raw.items()}
            dc = DeliberationConfig(**session.config.get("deliberation", {}))

            doc_text = session.document_text
            if not doc_text:
                continue

            from protoneo.agents.types import Document as _Doc
            doc = _Doc(
                document_id=uuid.uuid4().hex,
                filename=session.config.get("metadata", {}).get("filename", "paper.pdf"),
                text=doc_text,
                markdown=session.document_markdown or "",
            )

            model_map_raw = {}
            for k, v in agent_configs_raw.items():
                if isinstance(v, dict) and v.get("model"):
                    model_map_raw[k] = v["model"]

            session.status = SessionStatus.RUNNING
            session.error = None
            await _session_manager.update(session)

            bus = _event_buses.get(sid)
            if not bus:
                bus = SessionEventBus()
                _event_buses[sid] = bus

            ctl = PipelineControl()
            _pipeline_controls[sid] = ctl

            try:
                await _run_graph_pipeline(
                    sid, doc, profile, model_map_raw,
                    ac, bus, ctl,
                    delib_config=dc,
                    graph_only=(session.current_stage == "pre_review"),
                )
                batch_bus.emit("paper_complete", {
                    "session_id": sid, "index": i + 1,
                    "total": len(failed_sids),
                })
            except Exception as e:
                logger.error("Retry failed for %s: %s", sid, e)
                session = await _session_manager.get(sid)
                if session and session.status != SessionStatus.FAILED:
                    session.status = SessionStatus.FAILED
                    session.error = str(e)
                    await _session_manager.update(session)
                batch_bus.emit("paper_failed", {
                    "session_id": sid, "error": str(e),
                })

        batch_bus.emit("batch_complete", {"total": len(failed_sids)})

    asyncio.create_task(_retry_failed_sequential())

    return {
        "batch_id": batch_id,
        "retried": len(failed_sids),
        "session_ids": failed_sids,
        "status": "running",
    }

# ── Launch Review on Existing Graph ─────────────────

class LaunchReviewBody(BaseModel):
    model_map: dict[str, str] = Field(default_factory=dict)
    conference: str = ""
    max_rounds: int = 0
    user_instructions: str = ""

@router.post("/sessions/{session_id}/launch-review")
async def launch_review(session_id: str, body: LaunchReviewBody | None = None):
    """Launch reviews on a session that already has a completed graph.

    Accepts optional body with new model_map, conference, max_rounds,
    and user_instructions to override the original session config.
    """
    _session_manager = get_session_manager()
    _event_buses = get_event_buses()
    _pipeline_controls = get_pipeline_controls()

    session = await _session_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.knowledge_graph:
        raise HTTPException(status_code=400, detail="Session has no graph. Build or import a graph first.")

    # Allow overriding conference for the review
    conference_slug = (body.conference if body and body.conference
                      else session.config.get("metadata", {}).get("conference", "hpdc26"))
    try:
        profile = load_profile(conference_slug)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Conference profile '{conference_slug}' not found")

    # Rebuild agent configs with new model_map if provided
    if body and body.model_map:
        agent_configs = build_agent_configs(
            profile=profile, conference_slug=conference_slug,
            model_map=body.model_map,
        )
        # Persist the new config
        session.config["agents"] = {k: v.model_dump() for k, v in agent_configs.items()}
        if body.user_instructions:
            session.config["metadata"]["user_instructions"] = body.user_instructions
        await _session_manager.update(session)
    else:
        agent_configs_raw = session.config.get("agents", {})
        agent_configs = {k: AgentConfig(**v) for k, v in agent_configs_raw.items()}
    delib_raw = session.config.get("deliberation", {})
    if delib_raw and delib_raw.get("phases"):
        delib_config = DeliberationConfig(**delib_raw)
    else:
        reviewer_ids = [k for k in agent_configs if k != "meta"]
        delib_config = build_deliberation_config(
            reviewer_ids=reviewer_ids,
            max_rounds=body.max_rounds if body and body.max_rounds else 2,
        )

    pg = KnowledgeGraph.model_validate(session.knowledge_graph)

    ctx = _session_manager.get_context(session_id)
    doc_text = session.document_text or (ctx.documents[0].text if ctx.documents else "")

    doc_markdown = session.document_markdown or ""
    doc_proxy = _MinimalDoc(doc_text, doc_markdown, session.config.get("metadata", {}).get("filename", "paper.pdf"))
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
                sess.knowledge_graph = pg.model_dump(mode="json")
                sess.current_stage = "review"
                sess.status = SessionStatus.COMPLETED
                await _session_manager.update(sess)

            get_session_graphs()[sid] = pg.to_d3_format()
            ctl.stage_done("review")
            bus.emit("stage_complete", {"stage": "review"})
            bus.emit("completed", {"result": sess.result if sess else {}})

        except asyncio.CancelledError:
            bus.emit("pipeline_cancelled", {"message": "Review cancelled"})
        except Exception as e:
            logger.error("Review failed for session %s: %s", sid, e, exc_info=True)
            bus.emit("error", {"detail": str(e)})
        finally:
            get_pipeline_controls().pop(sid, None)

    task = asyncio.create_task(_run_review_only(session_id))
    ctl.set_task(task)

    return {
        "session_id": session_id,
        "status": "running",
        "stage": "review",
    }

# ── Post-Review: Refine, Score Lightpass, Persist ──

_FIELD_LABELS = {
    "paper_summary": "Paper Summary",
    "strengths": "Strengths",
    "weaknesses": "Weaknesses",
    "comments_for_authors": "Comments for Authors",
    "comments_for_pc": "Comments for PC",
}

def _build_review_context(session) -> str:
    """Assemble full context from paper, graph, and reviewer outputs."""
    parts = []
    paper_md = session.document_markdown or session.document_text
    if paper_md:
        parts.append(f"## Paper Content\n\n{paper_md}")
    if session.knowledge_graph:
        gs = session.knowledge_graph.get("summary", "")
        if gs:
            parts.append(f"## Knowledge Graph Summary\n\n{gs}")
    phases = session.result.get("phases", [])
    rp = []
    for phase in phases:
        for output in phase.get("outputs", []):
            role = output.get("agent_role", "reviewer")
            content = output.get("content", "")
            if content:
                rp.append(f"### {role}\n{content}")
    if rp:
        parts.append("## Review Committee Outputs\n\n" + "\n\n---\n\n".join(rp))
    return "\n\n".join(parts)

def _resolve_chat_model(session) -> str:
    """Pick the model for post-review interactions from session config."""
    agent_cfgs = session.config.get("agents", {})
    meta_cfg = agent_cfgs.get("meta", {})
    if isinstance(meta_cfg, dict) and meta_cfg.get("model"):
        return meta_cfg["model"]
    for cfg in agent_cfgs.values():
        if isinstance(cfg, dict) and cfg.get("model"):
            return cfg["model"]
    return ""

class RefineFieldRequest(BaseModel):
    field: str
    instruction: str
    current_fields: dict[str, Any] = Field(default_factory=dict)

@router.post("/sessions/{session_id}/refine-field")
async def refine_field(session_id: str, body: RefineFieldRequest):
    """Stream a refined version of one review field via WebSocket."""
    _session_manager = get_session_manager()
    _llm_client = get_llm_client()
    _event_buses = get_event_buses()

    session = await _session_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.result:
        raise HTTPException(status_code=409, detail="No review results")

    context = _build_review_context(session)
    chat_model = _resolve_chat_model(session)

    label = _FIELD_LABELS.get(body.field, body.field)
    current = body.current_fields.get(body.field, "")

    system_prompt = (
        "You are the PC Chair for HPDC 2026. You are editing one field "
        "of the unified final review. Output ONLY the revised text for "
        "this field. No JSON wrapper, no field label, just plain text.\n\n"
        + context
    )
    user_msg = (
        f'Revise the "{label}" field.\n\n'
        f"Current content:\n{current}\n\n"
        f"Instruction: {body.instruction}\n\n"
        "Current review state for consistency:\n"
        + "\n".join(
            f"[{k}]: {v}" for k, v in body.current_fields.items()
            if isinstance(v, str) and k != body.field
        )
    )

    bus = _event_buses.get(session_id)
    if not bus:
        bus = SessionEventBus()
        _event_buses[session_id] = bus

    bus.emit("refine_start", {"field": body.field, "model": chat_model})

    async def _stream():
        try:
            response = ""
            async for chunk in _llm_client.stream(
                model=chat_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                session_id=session_id,
            ):
                response += chunk
                bus.emit("refine_token", {"field": body.field, "chunk": chunk})
            bus.emit("refine_done", {
                "field": body.field, "content": response.strip(),
                "model": chat_model,
            })
        except Exception as e:
            logger.error("Refine failed for %s/%s: %s", session_id, body.field, e)
            bus.emit("refine_error", {"field": body.field, "detail": str(e)})

    asyncio.create_task(_stream())
    return {"status": "streaming", "field": body.field, "model": chat_model}

class ScoreLightpassRequest(BaseModel):
    new_score: int
    new_label: str
    current_fields: dict[str, Any] = Field(default_factory=dict)

@router.post("/sessions/{session_id}/score-lightpass")
async def score_lightpass(session_id: str, body: ScoreLightpassRequest):
    """Suggest field edits after a score change. Returns suggestions dict."""
    _session_manager = get_session_manager()
    _llm_client = get_llm_client()

    session = await _session_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.result:
        raise HTTPException(status_code=409, detail="No review results")

    context = _build_review_context(session)
    chat_model = _resolve_chat_model(session)

    system_prompt = (
        "You are the PC Chair for HPDC 2026. The reviewer changed the "
        "overall merit score. Review the text fields and suggest minimal "
        "edits so the tone and substance align with the new score. "
        "Only change fields where the current text contradicts the new score.\n\n"
        + context
    )

    fields_text = "\n\n".join(
        f"### {k}\n{v}" for k, v in body.current_fields.items()
        if isinstance(v, str) and v
    )
    user_msg = (
        f"The overall merit score is now {body.new_score} ({body.new_label}).\n\n"
        f"Current review fields:\n{fields_text}\n\n"
        "Return a JSON object mapping field names to their suggested new content. "
        "Only include fields that need changes. If nothing needs changing, "
        'return {}. Output ONLY the JSON object.'
    )

    try:
        response = ""
        async for chunk in _llm_client.stream(
            model=chat_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            session_id=session_id,
        ):
            response += chunk

        import re as _re
        suggestions = {}
        match = _re.search(r"\{[\s\S]*\}", response)
        if match:
            try:
                suggestions = json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return {"suggestions": suggestions, "model": chat_model}

    except Exception as e:
        logger.error("Score lightpass failed for %s: %s", session_id, e)
        raise HTTPException(status_code=500, detail=f"Lightpass failed: {e}")

class UpdateFinalReviewRequest(BaseModel):
    final_review: dict[str, Any]

@router.post("/sessions/{session_id}/update-final-review")
async def update_final_review(session_id: str, body: UpdateFinalReviewRequest):
    """Persist manual edits to the final review fields."""
    _session_manager = get_session_manager()
    session = await _session_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not session.result:
        raise HTTPException(status_code=409, detail="No review results")

    session.result["final_review"] = body.final_review
    session.result["pc_chair_review"] = body.final_review.get(
        "comments_for_authors", ""
    )
    await _session_manager.update(session)
    return {"status": "saved"}

# ── Graph Import ──────────────────────────────────

@router.post("/review-with-graph")
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
        graph_dict = graph_data["graph"]
        paper_title = graph_data.get("paper_title", "")
        imported_markdown = graph_data.get("document_markdown", graph_data.get("paper_markdown", ""))
    else:
        graph_dict = graph_data
        paper_title = ""
        imported_markdown = ""

    try:
        pg = KnowledgeGraph.model_validate(graph_dict)
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

    session = await _session_manager.create(
        config={
            "agents": {k: v.model_dump() for k, v in agent_configs.items()},
            "deliberation": delib_config.model_dump(),
            "metadata": {
                "type": "panel_review",
                "conference": conference,
                "filename": graph_data.get("paper_title", "imported-graph"),
                "paper_title": paper_title,
            },
        },
        app_name=_APP_NAME,
        app_version=_APP_VERSION,
    )
    session.knowledge_graph = graph_dict
    session.document_markdown = imported_markdown
    session.document_text = imported_markdown
    session.graph_source = "imported"
    if not pg.summary:
        pg.summary = pg.to_agent_briefing()
        session.knowledge_graph = pg.model_dump(mode="json")
    await _session_manager.update(session)

    # Build enriched message with paper content (not just graph summary)
    if imported_markdown:
        enriched_message = (
            f"{'=' * 60}\nMANUSCRIPT\n{'=' * 60}\n\n"
            + imported_markdown
            + "\n\n" + pg.summary
        )
    else:
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
                sess.knowledge_graph = pg.model_dump(mode="json")
                sess.current_stage = "review"
                sess.status = SessionStatus.COMPLETED
                await _session_manager.update(sess)

            get_session_graphs()[sid] = pg.to_d3_format()
            ctl.stage_done("review")
            bus.emit("stage_complete", {"stage": "review"})
            bus.emit("completed", {"result": sess.result if sess else {}})
        except asyncio.CancelledError:
            bus.emit("pipeline_cancelled", {"message": "Review cancelled"})
        except Exception as e:
            logger.error("Review failed for session %s: %s", sid, e, exc_info=True)
            bus.emit("error", {"detail": str(e)})
        finally:
            get_pipeline_controls().pop(sid, None)

    task = asyncio.create_task(_run_imported_review(session.session_id))
    ctl.set_task(task)

    return {
        "session_id": session.session_id,
        "status": "running",
        "graph_source": "imported",
        "node_count": len(pg.nodes),
        "edge_count": len(pg.edges),
    }


# ── Review Packet ──────────────────────────────────

@router.get("/sessions/{session_id}/review-packet")
async def get_review_packet(session_id: str):
    """Get the structured review packet for a completed panel session."""
    _session_manager = get_session_manager()
    session = await _session_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not _is_completed(session):
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
    final_review = session.result.get("final_review", {})
    prov_args = _extract_provenance_args(session)
    packet = result_to_packet(result, profile, paper_title, final_review=final_review, **prov_args)

    if session.knowledge_graph:
        try:
            pg = KnowledgeGraph.model_validate(session.knowledge_graph)
            packet.graph_summary = pg.summary
            packet.graph_node_count = len(pg.nodes)
            packet.graph_edge_count = len(pg.edges)

            if packet.reviews:
                review_dicts = [r.model_dump() for r in packet.reviews]
                packet.graph_utilization = pg.compute_utilization(review_dicts)
        except Exception:
            pass

    data = packet.model_dump(mode="json")
    data["final_review"] = session.result.get("final_review", {})
    return data

@router.get("/sessions/{session_id}/review-packet.md")
async def get_review_packet_md(session_id: str):
    """Export the review packet as Markdown."""
    _session_manager = get_session_manager()
    session = await _session_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not _is_completed(session):
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
    final_review = session.result.get("final_review", {})
    prov_args = _extract_provenance_args(session)
    packet = result_to_packet(result, profile, paper_title, final_review=final_review, **prov_args)
    md = packet_to_markdown(packet)

    return Response(
        content=md,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="review-packet-{session_id}.md"'
        },
    )

@router.get("/sessions/{session_id}/review-packet.pdf")
async def get_review_packet_pdf(session_id: str):
    """Export the review packet as PDF."""
    _session_manager = get_session_manager()
    session = await _session_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if not _is_completed(session):
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
    final_review = session.result.get("final_review", {})
    prov_args = _extract_provenance_args(session)
    packet = result_to_packet(result, profile, paper_title, final_review=final_review, **prov_args)
    pdf_bytes = packet_to_pdf(packet)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="review-packet-{session_id}.pdf"'
        },
    )
