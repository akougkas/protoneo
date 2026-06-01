"""
Paper Review API routes.

Endpoints: review, batch, preflight, conferences, graph, pipeline control,
export, ontology, and all review-specific session operations.
"""

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
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
from protoneo.knowledge.chunker import chunk_document
from protoneo.knowledge.graph import KnowledgeGraph
from protoneo.knowledge.parser import parse_file
from protoneo.llm.errors import sanitize_error_message
from protoneo.llm.settings import build_vlm_config, vlm_status

from .conference import ConferenceProfile, list_profiles, load_profile
_APP_NAME = "paper_review"
_APP_VERSION = "0.1.0"
from .export import packet_to_markdown, packet_to_pdf, write_review_artifacts
from .pipeline import (
    _build_enriched_review_message,
    _run_graph_pipeline,
    _run_review_stage,
)
from .preflight import run_preflight
from .review import (
    artifact_description_assumed_from_status,
    build_agent_configs,
    build_deliberation_config,
    build_user_message,
    normalize_artifact_description_status,
    session_to_review_packet,
)
from .schemas import sanitize_final_review

logger = logging.getLogger("protoneo.paper_review.api")

router = APIRouter()
_preflight_jobs: dict[str, dict[str, Any]] = {}

_SAVED_GRAPH_FILENAME_RE = re.compile(
    r"(?P<session_id>[0-9a-f]{32})(?:_graph|-graph|\.graph)?\.json$",
    re.IGNORECASE,
)


@dataclass
class ImportedGraphPayload:
    graph: KnowledgeGraph
    paper_title: str = ""
    conference: str = ""
    document_markdown: str = ""
    document_text: str = ""
    source_format: str = "knowledge_graph"
    source_session_id: str = ""
    artifact_description_status: str = ""
    artifact_description_assumed_present: bool = False
    warnings: list[str] = field(default_factory=list)


class _MinimalDoc:
    """Lightweight document proxy for build_user_message() when
    the full Document model is unavailable (e.g., launch-review
    on a session that only has persisted text)."""

    def __init__(self, text: str, markdown: str, filename: str):
        self.text = text
        self.markdown = markdown
        self.filename = filename
        self.chunks: list[str] = []


def _artifact_status_metadata(
    value: str = "",
    *,
    assumed_present: bool = False,
    existing: str = "",
) -> tuple[str, bool]:
    """Return canonical AD status and legacy assumption flag."""
    status = normalize_artifact_description_status(
        value or existing,
        assumed_present=assumed_present,
    )
    return status, artifact_description_assumed_from_status(status)


def _graph_title(pg: KnowledgeGraph, fallback: str = "") -> str:
    if fallback:
        return fallback
    if pg.paper_title:
        return pg.paper_title
    root = pg.node_by_id("paper-root")
    if root:
        return root.label
    paper = next((n for n in pg.nodes if n.node_type == "Paper"), None)
    return paper.label if paper else ""


def _extract_source_session_id(filename: str | None, graph_data: dict[str, Any]) -> str:
    explicit = graph_data.get("session_id") or graph_data.get("source_session_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    if not filename:
        return ""
    match = _SAVED_GRAPH_FILENAME_RE.search(Path(filename).name)
    return match.group("session_id") if match else ""


def _looks_like_d3_graph(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return False
    if not nodes:
        return "edges" in data or "links" in data
    first_node = nodes[0]
    return isinstance(first_node, dict) and (
        "uuid" in first_node
        or "name" in first_node
        or "display_name" in first_node
        or "links" in data
        or ("source_node_uuid" in str(data.get("edges", [])[:1]))
    )


def _should_ingest_as_d3_graph(data: Any) -> bool:
    if not _looks_like_d3_graph(data):
        return False
    nodes = data.get("nodes") or []
    first_node = nodes[0] if nodes else {}
    if isinstance(first_node, dict) and any(
        key in first_node for key in ("uuid", "name", "display_name", "type")
    ):
        return True
    edges = data.get("edges") or data.get("links") or []
    first_edge = edges[0] if edges else {}
    return isinstance(first_edge, dict) and any(
        key in first_edge
        for key in ("source_node_uuid", "target_node_uuid", "source", "target", "name", "fact_type")
    )


def _parse_imported_graph_payload(
    graph_data: dict[str, Any],
    *,
    filename: str | None = None,
) -> ImportedGraphPayload:
    """Normalize supported graph import formats into a KnowledgeGraph.

    Supported inputs:
    - canonical export wrapper: {schema_version, graph, document_markdown, ...}
    - raw KnowledgeGraph.model_dump()
    - GraphPanel/D3 snapshot: {nodes: [{uuid, name, type}], edges: [...]}
    """
    if not isinstance(graph_data, dict):
        raise ValueError("Graph import must be a JSON object")

    wrapped = "graph" in graph_data and isinstance(graph_data.get("graph"), dict)
    graph_dict = graph_data["graph"] if wrapped else graph_data
    payload = ImportedGraphPayload(
        graph=KnowledgeGraph(),
        paper_title=str(graph_data.get("paper_title") or ""),
        conference=str(graph_data.get("conference") or ""),
        document_markdown=str(graph_data.get("document_markdown") or graph_data.get("paper_markdown") or ""),
        document_text=str(graph_data.get("document_text") or ""),
        source_session_id=_extract_source_session_id(filename, graph_data),
        artifact_description_status=str(graph_data.get("artifact_description_status") or ""),
        artifact_description_assumed_present=bool(
            graph_data.get("artifact_description_assumed_present", False)
        ),
    )

    if _should_ingest_as_d3_graph(graph_dict):
        pg = KnowledgeGraph()
        pg.ingest_d3_data(graph_dict)
        payload.graph = pg
        payload.source_format = "d3_graph_export" if wrapped else "d3_graph"
    else:
        try:
            payload.graph = KnowledgeGraph.model_validate(graph_dict)
            payload.source_format = "knowledge_graph_export" if wrapped else "knowledge_graph"
        except Exception as kg_error:
            raise ValueError(f"Invalid graph data: {kg_error}") from kg_error

    payload.paper_title = _graph_title(payload.graph, payload.paper_title)
    if payload.paper_title and not payload.graph.paper_title:
        payload.graph.paper_title = payload.paper_title

    if not payload.graph.summary:
        payload.graph.summary = payload.graph.to_agent_briefing()
    if not payload.graph.summary:
        payload.graph.summary = (
            f"Imported graph with {len(payload.graph.nodes)} nodes and "
            f"{len(payload.graph.edges)} edges. No reviewer-facing summary was present."
        )
        payload.warnings.append("imported graph did not include semantic summary")

    payload.graph.update_stats()
    return payload


async def _enrich_imported_graph_payload_from_source_session(
    payload: ImportedGraphPayload,
    session_manager: Any,
) -> ImportedGraphPayload:
    """Recover manuscript text/metadata when a saved graph filename points at a session."""
    if not payload.source_session_id:
        return payload
    source_session = await session_manager.get(payload.source_session_id)
    if not source_session:
        return payload

    if not payload.document_markdown:
        payload.document_markdown = source_session.document_markdown or source_session.document_text
    if not payload.document_text:
        payload.document_text = source_session.document_text or source_session.document_markdown

    metadata = source_session.config.get("metadata", {}) if source_session.config else {}
    if not payload.artifact_description_status:
        payload.artifact_description_status = str(metadata.get("artifact_description_status") or "")
    if not payload.artifact_description_assumed_present:
        payload.artifact_description_assumed_present = bool(
            metadata.get("artifact_description_assumed_present", False)
        )
    if not payload.paper_title:
        payload.paper_title = metadata.get("paper_title") or metadata.get("filename") or ""
    if not payload.conference:
        payload.conference = metadata.get("conference", "")
    payload.warnings.append(f"recovered manuscript text from session {payload.source_session_id}")
    return payload


def _is_completed(session) -> bool:
    """Check if a session has completed, handling both enum and string status."""
    return session.status in (SessionStatus.COMPLETED, SessionStatus.COMPLETED.value)


def _session_pipeline_mode(session) -> str:
    """Infer intended pipeline mode for retry/resume compatibility."""
    metadata = session.config.get("metadata", {})
    mode = metadata.get("pipeline_mode", "")
    if mode in {"graph_only", "full_review", "imported_graph_review"}:
        return mode
    if getattr(session, "graph_source", "") == "imported":
        return "imported_graph_review"
    if session.config.get("deliberation"):
        return "full_review"
    return "graph_only"


def _parse_provenance(
    doc,
    *,
    fast_parse: bool,
    vlm: dict[str, Any] | None,
    duration_seconds: float,
) -> dict[str, Any]:
    """Build app-owned parse provenance from parser metadata."""
    metadata = getattr(doc, "metadata", {}) or {}
    figures = metadata.get("figures") or []
    tables = metadata.get("tables") or []
    figure_image_paths = [
        f.get("image_path", "")
        for f in figures
        if isinstance(f, dict) and f.get("image_path")
    ]
    artifacts = [
        artifact
        for artifact in [*figures, *tables]
        if isinstance(artifact, dict)
    ]
    described = sum(1 for artifact in artifacts if artifact.get("description"))
    total_artifacts = len(artifacts)
    if total_artifacts == 0:
        grounding_mode = "no_artifacts"
    elif described == 0:
        grounding_mode = "text_only"
    elif described == total_artifacts:
        grounding_mode = "vision_grounded"
    else:
        grounding_mode = "mixed"

    def _artifact_summary(artifact: dict[str, Any]) -> dict[str, Any]:
        return {
            key: artifact.get(key)
            for key in (
                "index",
                "page",
                "caption",
                "description",
                "description_source",
                "numeric_claims",
                "grounding",
            )
        }

    return {
        "parser": metadata.get("parser", ""),
        "fast_parse": fast_parse,
        "vlm": {
            "enabled": bool(vlm and not fast_parse),
            "endpoint": (vlm or {}).get("url", ""),
            "model": (vlm or {}).get("model", ""),
            "temperature": (vlm or {}).get("temperature"),
            "top_p": (vlm or {}).get("top_p"),
            "timeout": (vlm or {}).get("timeout"),
            "concurrency": (vlm or {}).get("concurrency"),
        },
        "figure_count": len(figures) if isinstance(figures, list) else 0,
        "figure_image_paths": figure_image_paths,
        "table_count": metadata.get("table_count", 0),
        "grounding_mode": grounding_mode,
        "artifacts_described": described,
        "artifacts_total": total_artifacts,
        "figures": [_artifact_summary(f) for f in figures if isinstance(f, dict)],
        "tables": [_artifact_summary(t) for t in tables if isinstance(t, dict)],
        "figures_dir": metadata.get("figures_dir", ""),
        "duration_seconds": round(duration_seconds, 3),
    }


async def _run_review_only_pipeline(
    sid: str,
    profile: ConferenceProfile,
    agent_configs: dict[str, AgentConfig],
    delib_config: DeliberationConfig,
    bus: SessionEventBus,
    ctl: PipelineControl,
) -> None:
    """Run only review stages for sessions with an existing graph."""
    _session_manager = get_session_manager()
    session = await _session_manager.get(sid)
    if not session or not session.knowledge_graph:
        raise RuntimeError("Session has no graph. Build or import a graph first.")

    pg = KnowledgeGraph.model_validate(session.knowledge_graph)
    doc_text = session.document_text or session.document_markdown or pg.summary
    doc_markdown = session.document_markdown or doc_text
    doc_proxy = _MinimalDoc(
        doc_text,
        doc_markdown,
        session.config.get("metadata", {}).get("filename", "paper.pdf"),
    )
    user_message = build_user_message(doc_proxy, profile)
    enriched_message = _build_enriched_review_message(user_message, pg)

    ctl.enter_stage("review")
    bus.emit("stage_started", {
        "stage": "review",
        "step": "independent_reviews",
        "message": "Starting peer review...",
    })
    ctl.enter_step("independent_reviews")
    bus.emit("step_started", {
        "stage": "review",
        "step": "independent_reviews",
        "message": "Starting independent peer reviews...",
    })

    await _run_review_stage(
        sid,
        agent_configs,
        delib_config,
        enriched_message,
        bus,
        ctl,
        pg,
    )

    session = await _session_manager.get(sid)
    if session:
        session.knowledge_graph = pg.model_dump(mode="json")
        session.current_stage = "review"
        session.status = SessionStatus.COMPLETED
        await _session_manager.update(session)

    get_session_graphs()[sid] = pg.to_d3_format()
    ctl.stage_done("review")
    bus.emit("stage_complete", {"stage": "review"})
    bus.emit("completed", {"result": session.result if session else {}})


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
    conference: str = Form(...),
):
    """Start fast preflight checks and return a progress job id."""
    try:
        profile = load_profile(conference)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail=f"Conference profile '{conference}' not found"
        )

    content = await file.read()
    filename = file.filename or "paper.pdf"
    job_id = uuid.uuid4().hex
    _preflight_jobs[job_id] = {
        "status": "queued",
        "progress": 0,
        "stage": "queued",
        "result": None,
        "error": "",
    }

    async def _run() -> None:
        file_path: Path | None = None
        try:
            _preflight_jobs[job_id].update(
                status="running",
                stage="probing_vlm",
                progress=10,
            )
            status = vlm_status()

            _preflight_jobs[job_id].update(stage="parsing", progress=30)
            upload_dir = _get_upload_dir()
            safe_name = f"{uuid.uuid4().hex}_{filename}"
            file_path = upload_dir / safe_name
            file_path.write_bytes(content)

            loop = asyncio.get_running_loop()
            doc = await loop.run_in_executor(
                None,
                lambda: parse_file(str(file_path), fast=True, vlm_config=build_vlm_config()),
            )
            file_path.unlink(missing_ok=True)

            _preflight_jobs[job_id].update(stage="checks", progress=80)
            result = run_preflight(
                doc.text,
                doc.filename,
                profile,
                figure_count=len(doc.metadata.get("figures") or []),
                table_count=int(
                    doc.metadata.get("table_count")
                    or len(doc.metadata.get("tables") or [])
                ),
                vlm_status=status,
            )
            payload = result.model_dump(mode="json")
            payload["vlm_status"] = status
            _preflight_jobs[job_id].update(
                status="done",
                stage="done",
                progress=100,
                result=payload,
            )
        except Exception as exc:  # noqa: BLE001
            if file_path:
                file_path.unlink(missing_ok=True)
            logger.warning("Preflight job %s failed: %s", job_id, exc)
            _preflight_jobs[job_id].update(
                status="error",
                stage="error",
                progress=100,
                error=str(exc),
            )

    asyncio.create_task(_run())
    return {"job_id": job_id}


@router.get("/preflight/{job_id}")
async def preflight_status(job_id: str):
    job = _preflight_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown preflight job")
    return job

# ── Review Sessions ────────────────────────────────────

@router.post("/start-review")
@router.post("/sessions/upload")
@router.post("/review")
async def start_panel_review(
    file: UploadFile = File(...),
    conference: str = Form(...),
    model_map_json: str = Form("{}"),
    max_rounds: int = Form(2),
    user_instructions: str = Form(""),
    skip_graph: bool = Form(False),
    fast_parse: bool = Form(False),
    artifact_description_assumed_present: bool = Form(False),
    artifact_description_status: str = Form(""),
):
    """Create and start a full Paper Review session.

    Returns immediately with session_id. PDF parsing runs in the
    background so the UI can navigate to the session page. Docling
    handles layout extraction with optional VLM figure descriptions.
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
    ad_status, ad_assumed_present = _artifact_status_metadata(
        artifact_description_status,
        assumed_present=artifact_description_assumed_present,
    )

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
        artifact_description_assumed_present=ad_assumed_present,
        artifact_description_status=ad_status,
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
                "pipeline_mode": "full_review",
                "conference": conference,
                "filename": file.filename,
                "paper_title": "",
                "artifact_description_status": ad_status,
                "artifact_description_assumed_present": ad_assumed_present,
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
            vlm = build_vlm_config()
            doc = await loop.run_in_executor(
                None, lambda: parse_file(str(file_path), fast=fast_parse, vlm_config=vlm),
            )
            doc = chunk_document(doc)
            session = await _session_manager.get(sid)
            if session:
                existing = session.pipeline_steps.get("parse") or {}
                completed_at = _time.monotonic()
                session.pipeline_steps["parse"] = StepState(
                    status="complete",
                    started_at=existing.get("started_at"),
                    completed_at=completed_at,
                    model_used=vlm.get("model", "") if vlm and not fast_parse else "",
                ).model_dump()
                started_at = existing.get("started_at") or completed_at
                session.app_data["parse"] = _parse_provenance(
                    doc,
                    fast_parse=fast_parse,
                    vlm=vlm,
                    duration_seconds=completed_at - float(started_at),
                )
                await _session_manager.update(session)
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
    conference: str = Form(...),
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
                    "pipeline_mode": "graph_only",
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
                vlm = build_vlm_config()
                doc = await loop.run_in_executor(
                    None, lambda: parse_file(str(fpath), fast=fast_parse, vlm_config=vlm),
                )
                doc = chunk_document(doc)
                session = await _session_manager.get(sid)
                if session:
                    existing = session.pipeline_steps.get("parse") or {}
                    completed_at = _time.monotonic()
                    session.pipeline_steps["parse"] = StepState(
                        status="complete",
                        started_at=existing.get("started_at"),
                        completed_at=completed_at,
                        model_used=vlm.get("model", "") if vlm and not fast_parse else "",
                    ).model_dump()
                    started_at = existing.get("started_at") or completed_at
                    session.app_data["parse"] = _parse_provenance(
                        doc,
                        fast_parse=fast_parse,
                        vlm=vlm,
                        duration_seconds=completed_at - float(started_at),
                    )
                    await _session_manager.update(session)
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
    conference: str = Form(...),
    model_map_json: str = Form("{}"),
    max_rounds: int = Form(2),
    user_instructions: str = Form(""),
    fast_parse: bool = Form(False),
    artifact_description_assumed_present: bool = Form(False),
    artifact_description_status: str = Form("not_provided_to_protoneo"),
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
    ad_status, ad_assumed_present = _artifact_status_metadata(
        artifact_description_status,
        assumed_present=artifact_description_assumed_present,
    )

    agent_configs = build_agent_configs(
        profile=profile, conference_slug=conference,
        model_map=model_map if model_map else None,
        user_instructions=user_instructions,
        artifact_description_assumed_present=ad_assumed_present,
        artifact_description_status=ad_status,
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
                    "pipeline_mode": "full_review",
                    "conference": conference,
                    "filename": file.filename,
                    "paper_title": "",
                    "artifact_description_status": ad_status,
                    "artifact_description_assumed_present": ad_assumed_present,
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
                vlm = build_vlm_config()
                doc = await loop.run_in_executor(
                    None, lambda: parse_file(str(fpath), fast=fast_parse, vlm_config=vlm),
                )
                doc = chunk_document(doc)
                session = await _session_manager.get(sid)
                if session:
                    existing = session.pipeline_steps.get("parse") or {}
                    completed_at = _time.monotonic()
                    session.pipeline_steps["parse"] = StepState(
                        status="complete",
                        started_at=existing.get("started_at"),
                        completed_at=completed_at,
                        model_used=vlm.get("model", "") if vlm and not fast_parse else "",
                    ).model_dump()
                    started_at = existing.get("started_at") or completed_at
                    session.app_data["parse"] = _parse_provenance(
                        doc,
                        fast_parse=fast_parse,
                        vlm=vlm,
                        duration_seconds=completed_at - float(started_at),
                    )
                    await _session_manager.update(session)
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
    pipeline_mode = _session_pipeline_mode(session)

    # Reconstruct document from persisted data
    doc_text = session.document_text or session.document_markdown
    doc_md = session.document_markdown or ""
    filename = session.config.get("metadata", {}).get("filename", "paper.pdf")
    if not doc_text and pipeline_mode != "imported_graph_review":
        raise HTTPException(status_code=400, detail="No paper text stored. Cannot retry.")

    from protoneo.agents.types import Document as _Doc
    doc = _Doc(
        document_id=uuid.uuid4().hex,
        filename=filename,
        text=doc_text or "",
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

    if pipeline_mode == "imported_graph_review" and session.knowledge_graph:
        task = asyncio.create_task(_run_review_only_pipeline(
            session_id,
            profile,
            agent_configs,
            delib_config,
            bus,
            ctl,
        ))
        ctl.set_task(task)
        return {"session_id": session_id, "status": "running", "action": "retry"}

    # Full-review retries should continue through the review stage without
    # stopping at the pre-review gate.
    graph_only = pipeline_mode == "graph_only"
    if not graph_only:
        ctl.skip_gate = True
    task = asyncio.create_task(_run_graph_pipeline(
        session_id, doc, profile, model_map_raw,
        agent_configs, bus, ctl,
        delib_config=delib_config,
        graph_only=graph_only,
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
            pipeline_mode = _session_pipeline_mode(session)

            doc_text = session.document_text or session.document_markdown
            if not doc_text and pipeline_mode != "imported_graph_review":
                continue

            from protoneo.agents.types import Document as _Doc
            doc = _Doc(
                document_id=uuid.uuid4().hex,
                filename=session.config.get("metadata", {}).get("filename", "paper.pdf"),
                text=doc_text or "",
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
            graph_only = pipeline_mode == "graph_only"
            if not graph_only:
                ctl.skip_gate = True

            try:
                if pipeline_mode == "imported_graph_review" and session.knowledge_graph:
                    await _run_review_only_pipeline(sid, profile, ac, dc, bus, ctl)
                else:
                    await _run_graph_pipeline(
                        sid, doc, profile, model_map_raw,
                        ac, bus, ctl,
                        delib_config=dc,
                        graph_only=graph_only,
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
    model_map: dict[str, Any] = Field(default_factory=dict)
    conference: str = ""
    max_rounds: int = 0
    user_instructions: str = ""
    artifact_description_assumed_present: bool = False
    artifact_description_status: str = ""

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

    metadata = session.config.setdefault("metadata", {})
    existing_status = str(metadata.get("artifact_description_status") or "")
    ad_status, ad_assumed_present = _artifact_status_metadata(
        body.artifact_description_status if body else "",
        assumed_present=body.artifact_description_assumed_present if body else False,
        existing=existing_status,
    )

    # Rebuild agent configs with new model_map or review metadata if provided.
    if body and (
        body.model_map
        or body.user_instructions
        or body.artifact_description_assumed_present
        or body.artifact_description_status
    ):
        agent_configs = build_agent_configs(
            profile=profile, conference_slug=conference_slug,
            model_map=body.model_map if body.model_map else None,
            user_instructions=body.user_instructions,
            artifact_description_assumed_present=ad_assumed_present,
            artifact_description_status=ad_status,
        )
        # Persist the new config
        session.config["agents"] = {k: v.model_dump() for k, v in agent_configs.items()}
        if body.user_instructions:
            metadata["user_instructions"] = body.user_instructions
        metadata["artifact_description_status"] = ad_status
        metadata["artifact_description_assumed_present"] = ad_assumed_present
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
    from .pipeline import _build_enriched_review_message
    enriched_message = _build_enriched_review_message(user_message, pg)

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
            error = sanitize_error_message(e)
            logger.error("Review failed for session %s: %s", sid, error, exc_info=True)
            bus.emit("error", {"detail": error})
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
        "You are editing one field of the unified final review. "
        "Output ONLY the revised text for "
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
        "You are calibrating the unified final review after the overall "
        "merit score changed. Review the text fields and suggest minimal "
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

    final_review = sanitize_final_review(body.final_review)
    session.result["final_review"] = final_review
    session.result["pc_chair_review"] = final_review
    session.app_data["final_review"] = final_review
    session.app_data.pop("review_packet", None)
    await _session_manager.update(session)
    return {"status": "saved"}

# ── Graph Import ──────────────────────────────────

@router.post("/review-with-graph")
async def review_with_graph(
    graph_file: UploadFile = File(...),
    conference: str = Form(...),
    model_map_json: str = Form("{}"),
    max_rounds: int = Form(2),
    user_instructions: str = Form(""),
    artifact_description_assumed_present: bool = Form(False),
    artifact_description_status: str = Form(""),
):
    """Create a review-ready session with an imported graph.

    Importing a graph must not launch reviewers. Review launch is the explicit
    next action through /sessions/{session_id}/launch-review.
    """
    _session_manager = get_session_manager()

    try:
        profile = load_profile(conference)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Conference profile '{conference}' not found")

    graph_content = await graph_file.read()
    try:
        graph_data = json.loads(graph_content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in graph file")

    try:
        imported = _parse_imported_graph_payload(graph_data, filename=graph_file.filename)
        imported = await _enrich_imported_graph_payload_from_source_session(imported, _session_manager)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    pg = imported.graph
    paper_title = imported.paper_title
    imported_markdown = imported.document_markdown or imported.document_text

    try:
        model_map = json.loads(model_map_json) if model_map_json else {}
    except json.JSONDecodeError:
        model_map = {}
    ad_status, ad_assumed_present = _artifact_status_metadata(
        artifact_description_status,
        assumed_present=artifact_description_assumed_present
        or imported.artifact_description_assumed_present,
        existing=imported.artifact_description_status,
    )

    agent_configs = build_agent_configs(
        profile=profile, conference_slug=conference,
        model_map=model_map if model_map else None,
        user_instructions=user_instructions,
        artifact_description_assumed_present=ad_assumed_present,
        artifact_description_status=ad_status,
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
                "pipeline_mode": "imported_graph_review",
                "conference": conference or imported.conference,
                "filename": paper_title or Path(graph_file.filename or "imported-graph").name,
                "paper_title": paper_title,
                "graph_source": "imported",
                "graph_import_format": imported.source_format,
                "source_session_id": imported.source_session_id,
                "graph_import_warnings": imported.warnings,
                "artifact_description_status": ad_status,
                "artifact_description_assumed_present": ad_assumed_present,
            },
        },
        app_name=_APP_NAME,
        app_version=_APP_VERSION,
    )
    session.knowledge_graph = pg.model_dump(mode="json")
    session.document_markdown = imported_markdown
    session.document_text = imported.document_text or imported_markdown
    session.graph_source = "imported"
    session.current_stage = "pre_review"
    session.graph_after_step["imported_graph"] = pg.snapshot()
    session.pipeline_steps["imported_graph"] = StepState(
        status="complete",
        nodes_added=len(pg.nodes),
        edges_added=len(pg.edges),
    ).model_dump()
    await _session_manager.update(session)
    get_session_graphs()[session.session_id] = pg.to_d3_format()

    return {
        "session_id": session.session_id,
        "status": session.status.value,
        "pipeline_mode": "imported_graph_review",
        "graph_source": "imported",
        "graph_import_format": imported.source_format,
        "source_session_id": imported.source_session_id,
        "document_markdown_length": len(session.document_markdown),
        "node_count": len(pg.nodes),
        "edge_count": len(pg.edges),
    }


# ── SC26 Packet Review ──────────────────────────────

SC26_PACKET_IDS = (
    "pap111s2",
    "pap1162s2",
    "pap282s2",
    "pap440s2",
    "pap535s2",
    "pap616s2",
    "pap651s2",
)


class SC26PacketReviewBody(BaseModel):
    packet_root: str = "submission_packets_sc26"
    paper_ids: list[str] = Field(default_factory=list)
    conference: str = "sc26"
    model_map: dict[str, Any] = Field(default_factory=dict)
    preset: str = ""
    max_rounds: int = 2
    force: bool = False
    skip_completed: bool = True
    artifact_description_assumed_present: bool = True
    artifact_description_status: str = "submitted"
    user_instructions: str = ""


def _normalize_title_for_match(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _packet_review_template_path(packet_dir: Path) -> Path:
    paper_id = packet_dir.name
    direct = packet_dir / f"{paper_id}_review.txt"
    if direct.exists():
        return direct
    matches = sorted(packet_dir.glob("*_review.txt"))
    if not matches:
        raise FileNotFoundError(f"No Linklings review template found in {packet_dir}")
    return matches[0]


def _packet_pdf_path(packet_dir: Path) -> Path:
    paper_id = packet_dir.name
    direct = packet_dir / f"{paper_id}.pdf"
    if direct.exists():
        return direct
    matches = sorted(
        p for p in packet_dir.glob("*.pdf")
        if not p.name.endswith("_details.pdf")
    )
    return matches[0] if matches else Path()


def _packet_title_from_template(template_path: Path) -> str:
    text = template_path.read_text(errors="ignore")
    match = re.search(r"<<\s*submission reviewed:\s*\([^)]+\)\s*(.*?)\s*>>", text)
    return match.group(1).strip() if match else template_path.stem


def _packet_manifest_complete(packet_dir: Path) -> bool:
    out = packet_dir / "protoneo_outputs"
    manifest = out / "run_manifest.json"
    offline = out / f"{packet_dir.name}_protoneo_offline_review.txt"
    if not manifest.exists() or not offline.exists():
        return False
    try:
        data = json.loads(manifest.read_text())
    except Exception:
        return False
    return bool(data.get("completed"))


def _packet_dirs(packet_root: str | Path, paper_ids: list[str] | None = None) -> list[Path]:
    root = Path(packet_root)
    requested = paper_ids or list(SC26_PACKET_IDS)
    return [root / pid for pid in requested if (root / pid).is_dir()]


def _preset_model_map(preset: str) -> dict[str, str]:
    if not preset:
        return {}
    try:
        from protoneo.llm.settings import load_settings, resolve_preset

        resolved = resolve_preset(preset, load_settings())
        return dict(resolved.assignments) if resolved else {}
    except Exception as e:
        logger.warning("Could not resolve preset %s: %s", preset, e)
        return {}


def _load_graph_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _graph_match_title(path: Path) -> tuple[str, ImportedGraphPayload | None]:
    try:
        payload = _parse_imported_graph_payload(
            _load_graph_json(path),
            filename=path.name,
        )
        return payload.paper_title, payload
    except Exception as e:
        logger.debug("Skipping non-matching graph %s: %s", path, e)
        return "", None


def _locate_saved_graph_for_packet(packet_dir: Path, title: str) -> Path | None:
    out = packet_dir / "protoneo_outputs"
    paper_id = packet_dir.name
    local_candidates = [
        packet_dir / f"{paper_id}.graph.json",
        out / "imported_graph.json",
        out / "graph.json",
    ]
    for candidate in local_candidates:
        if candidate.exists():
            return candidate

    for candidate in sorted(packet_dir.glob("*.graph.json")):
        if candidate.is_file():
            return candidate

    graph_dir = Path("data/sessions/graphs")
    if not graph_dir.exists():
        return None

    target = _normalize_title_for_match(title)
    target_words = set(target.split())
    best: tuple[int, Path] | None = None
    for graph_path in sorted(graph_dir.glob("*.json")):
        graph_title, payload = _graph_match_title(graph_path)
        if not payload:
            continue
        candidate = _normalize_title_for_match(graph_title)
        if not candidate:
            continue
        score = 0
        if candidate == target:
            score = 1000
        elif target and (target in candidate or candidate in target):
            score = 750
        else:
            common = target_words & set(candidate.split())
            score = len(common)
        if score and (best is None or score > best[0]):
            best = (score, graph_path)
    return best[1] if best and best[0] >= 3 else None


async def _run_one_sc26_packet_review(
    packet_dir: Path,
    *,
    conference: str,
    model_map: dict[str, Any],
    preset: str,
    max_rounds: int,
    user_instructions: str,
    artifact_description_assumed_present: bool,
    artifact_description_status: str = "",
) -> dict[str, Any]:
    _session_manager = get_session_manager()
    template_path = _packet_review_template_path(packet_dir)
    paper_title = _packet_title_from_template(template_path)
    graph_path = _locate_saved_graph_for_packet(packet_dir, paper_title)
    if not graph_path:
        return {
            "paper_id": packet_dir.name,
            "status": "missing_graph",
            "message": f"No saved graph matched {paper_title!r}",
        }

    profile = load_profile(conference)
    graph_data = _load_graph_json(graph_path)
    imported = _parse_imported_graph_payload(graph_data, filename=graph_path.name)
    imported = await _enrich_imported_graph_payload_from_source_session(imported, _session_manager)

    resolved_model_map = {
        **_preset_model_map(preset),
        **(model_map or {}),
    }
    ad_status, ad_assumed_present = _artifact_status_metadata(
        artifact_description_status,
        assumed_present=artifact_description_assumed_present,
    )
    agent_configs = build_agent_configs(
        profile=profile,
        conference_slug=conference,
        model_map=resolved_model_map if resolved_model_map else None,
        user_instructions=user_instructions,
        artifact_description_assumed_present=ad_assumed_present,
        artifact_description_status=ad_status,
    )
    reviewer_ids = [k for k in agent_configs if k != "meta"]
    delib_config = build_deliberation_config(
        reviewer_ids=reviewer_ids,
        max_rounds=max_rounds,
    )

    pg = imported.graph
    imported_markdown = imported.document_markdown or imported.document_text
    paper_pdf = _packet_pdf_path(packet_dir)
    session = await _session_manager.create(
        config={
            "agents": {k: v.model_dump() for k, v in agent_configs.items()},
            "deliberation": delib_config.model_dump(),
            "metadata": {
                "type": "panel_review",
                "pipeline_mode": "imported_graph_review",
                "conference": conference,
                "filename": paper_pdf.name if paper_pdf else packet_dir.name,
                "paper_title": imported.paper_title or paper_title,
                "graph_source": "imported",
                "graph_import_format": imported.source_format,
                "source_session_id": imported.source_session_id,
                "source_graph_path": str(graph_path),
                "graph_import_warnings": imported.warnings,
                "packet_paper_id": packet_dir.name,
                "packet_dir": str(packet_dir),
                "artifact_description_status": ad_status,
                "artifact_description_assumed_present": ad_assumed_present,
                "preset": preset,
            },
        },
        app_name=_APP_NAME,
        app_version=_APP_VERSION,
    )
    session.knowledge_graph = pg.model_dump(mode="json")
    session.document_markdown = imported_markdown
    session.document_text = imported.document_text or imported_markdown
    session.graph_source = "imported"
    await _session_manager.update(session)

    if imported_markdown:
        doc_proxy = _MinimalDoc(
            imported_markdown,
            imported_markdown,
            paper_pdf.name if paper_pdf else packet_dir.name,
        )
        user_message = build_user_message(doc_proxy, profile)
    else:
        user_message = pg.summary
    enriched_message = _build_enriched_review_message(user_message, pg)

    bus = SessionEventBus()
    ctl = PipelineControl()
    await _run_review_stage(
        session.session_id,
        agent_configs,
        delib_config,
        enriched_message,
        bus,
        ctl,
        pg,
    )

    completed = await _session_manager.get(session.session_id)
    if completed:
        completed.knowledge_graph = pg.model_dump(mode="json")
        completed.current_stage = "review"
        completed.status = SessionStatus.COMPLETED
        await _session_manager.update(completed)
    else:
        raise RuntimeError(f"Session {session.session_id} disappeared during packet review")

    packet = session_to_review_packet(completed)
    output_dir = packet_dir / "protoneo_outputs"
    prompt_pack_version = ""
    try:
        from .prompts import load_prompt_pack

        prompt_pack_version = str(load_prompt_pack(conference).get("version", ""))
    except Exception:
        pass
    manifest = write_review_artifacts(
        packet,
        output_dir,
        source_graph=pg,
        template_path=template_path,
        paper_id=packet_dir.name,
        paper_path=paper_pdf,
        source_graph_path=graph_path,
        source_session_id=imported.source_session_id,
        model_map=resolved_model_map,
        preset=preset,
        prompt_pack_version=prompt_pack_version,
        artifact_description_assumed_present=ad_assumed_present,
        artifact_description_status=ad_status,
    )
    return {
        "paper_id": packet_dir.name,
        "status": "completed",
        "session_id": packet.session_id,
        "output_dir": str(output_dir),
        "manifest": manifest,
    }


async def run_sc26_packet_reviews(
    *,
    packet_root: str = "submission_packets_sc26",
    paper_ids: list[str] | None = None,
    conference: str = "sc26",
    model_map: dict[str, Any] | None = None,
    preset: str = "",
    max_rounds: int = 2,
    force: bool = False,
    skip_completed: bool = True,
    artifact_description_assumed_present: bool = True,
    artifact_description_status: str = "submitted",
    user_instructions: str = "",
) -> dict[str, Any]:
    results = []
    for packet_dir in _packet_dirs(packet_root, paper_ids):
        if skip_completed and not force and _packet_manifest_complete(packet_dir):
            results.append({
                "paper_id": packet_dir.name,
                "status": "skipped_completed",
                "output_dir": str(packet_dir / "protoneo_outputs"),
            })
            continue
        try:
            results.append(await _run_one_sc26_packet_review(
                packet_dir,
                conference=conference,
                model_map=model_map or {},
                preset=preset,
                max_rounds=max_rounds,
                user_instructions=user_instructions,
                artifact_description_assumed_present=artifact_description_assumed_present,
                artifact_description_status=artifact_description_status,
            ))
        except Exception as e:
            logger.error("SC26 packet review failed for %s: %s", packet_dir, e, exc_info=True)
            results.append({
                "paper_id": packet_dir.name,
                "status": "failed",
                "error": str(e),
            })
    return {
        "packet_root": str(packet_root),
        "conference": conference,
        "results": results,
    }


@router.post("/sc26/packet-review")
async def start_sc26_packet_review(body: SC26PacketReviewBody):
    """Run imported-graph review for SC26 packet folders.

    The task runs in the background because each paper may take minutes. Use
    the returned batch id to correlate logs; artifacts are written under each
    packet folder.
    """
    batch_id = uuid.uuid4().hex
    batch_bus = SessionEventBus()
    get_event_buses()[f"sc26_packet_{batch_id}"] = batch_bus

    async def _run() -> None:
        batch_bus.emit("batch_progress", {
            "batch_id": batch_id,
            "status": "running",
        })
        result = await run_sc26_packet_reviews(
            packet_root=body.packet_root,
            paper_ids=body.paper_ids or None,
            conference=body.conference,
            model_map=body.model_map,
            preset=body.preset,
            max_rounds=body.max_rounds,
            force=body.force,
            skip_completed=body.skip_completed,
            artifact_description_assumed_present=body.artifact_description_assumed_present,
            artifact_description_status=body.artifact_description_status,
            user_instructions=body.user_instructions,
        )
        batch_bus.emit("batch_complete", {
            "batch_id": batch_id,
            **result,
        })

    asyncio.create_task(_run())
    return {
        "batch_id": batch_id,
        "status": "running",
        "packet_root": body.packet_root,
        "paper_ids": body.paper_ids or list(SC26_PACKET_IDS),
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

    # Rebuild from the canonical session result so parser/schema hardening
    # applies to existing sessions instead of serving stale cached packets.
    try:
        packet = session_to_review_packet(session)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to build review packet: {e}"
        )

    data = packet.model_dump(mode="json")
    data["final_review"] = data.get("pc_chair_review", {})

    # Cache for subsequent requests
    session.app_data["review_packet"] = data
    await _session_manager.update(session)

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
        packet = session_to_review_packet(session)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to build review packet: {e}"
        )
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
        packet = session_to_review_packet(session)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to build review packet: {e}"
        )
    pdf_bytes = packet_to_pdf(packet)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="review-packet-{session_id}.pdf"'
        },
    )
