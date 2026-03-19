"""
Kernel API routes for ProtoNeo.

Endpoints: health, settings, models, providers (OAuth), sessions, WebSocket streaming, agents.
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..agents.types import Document
from ..config.schema import AgentConfig, DeliberationConfig, ProtoNeoConfig
from ..deliberation.engine import DeliberationEngine
from ..deliberation.session import SessionManager, SessionStatus
from ..knowledge.chunker import chunk_document
from ..knowledge.parser import parse_file
from ..llm.client import LLMClient
from ..llm.registry import CapabilityRegistry

logger = logging.getLogger("protoneo.api")


class SessionEventBus:
    """Per-session event bus with replay for late WebSocket subscribers.

    Events are buffered so that a WebSocket connecting after POST /api/panel/review
    receives all events that were emitted before the connection opened.
    """

    def __init__(self):
        self._history: list[dict] = []
        self._subscribers: list[asyncio.Queue] = []

    def emit(self, event_type: str, data: dict) -> None:
        event = {"type": event_type, **data}
        self._history.append(event)
        for q in self._subscribers:
            q.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue[dict] = asyncio.Queue()
        for event in self._history:
            q.put_nowait(event)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    @property
    def finished(self) -> bool:
        return any(e["type"] in ("completed", "error") for e in self._history)


class PipelineControl:
    """Per-session pipeline control for human-in-the-loop gating.

    The pipeline has 3 stages (pre_review, review, post_review) with a
    mandatory gate between pre_review and review where the user inspects
    the graph and clicks "Proceed to Review".
    """

    STAGES = ["pre_review", "review", "post_review"]

    PRE_REVIEW_STEPS = ["parse", "metadata", "ontology", "extract", "coref", "verify", "summarize"]
    REVIEW_STEPS = ["independent_reviews", "deliberation", "meta_review", "pc_chair"]

    def __init__(self):
        self.auto_advance: bool = True
        self.current_stage: str = ""
        self.current_step: str = ""
        self.completed_stages: list[str] = []
        self._gate: asyncio.Event = asyncio.Event()
        self._gate.set()
        self.paused: bool = False
        self.cancelled: bool = False
        self._task: asyncio.Task | None = None

    def set_task(self, task: asyncio.Task) -> None:
        self._task = task

    def enter_stage(self, stage: str) -> None:
        self.current_stage = stage
        self.current_step = ""

    def enter_step(self, step: str) -> None:
        self.current_step = step

    def stage_done(self, stage: str) -> None:
        if stage not in self.completed_stages:
            self.completed_stages.append(stage)
        self.current_step = ""

    async def wait_for_gate(self) -> None:
        """Block at the mandatory pre_review -> review gate."""
        self._gate.clear()
        self.paused = True
        await self._gate.wait()
        self.paused = False
        if self.cancelled:
            raise asyncio.CancelledError("Pipeline cancelled by user")

    async def wait_if_paused(self) -> None:
        """Block only if manually paused (not for mandatory gates)."""
        if not self.auto_advance:
            self._gate.clear()
            self.paused = True
            await self._gate.wait()
            self.paused = False
        if self.cancelled:
            raise asyncio.CancelledError("Pipeline cancelled by user")

    def advance(self) -> None:
        self.paused = False
        self._gate.set()

    def pause(self) -> None:
        self.auto_advance = False
        self.paused = True
        self._gate.clear()

    def resume(self) -> None:
        self.auto_advance = True
        self.paused = False
        self._gate.set()

    def cancel(self) -> None:
        self.cancelled = True
        self.paused = False
        self._gate.set()
        if self._task and not self._task.done():
            self._task.cancel()

    def status(self) -> dict:
        return {
            "current_stage": self.current_stage,
            "current_step": self.current_step,
            "completed_stages": self.completed_stages,
            "auto_advance": self.auto_advance,
            "paused": self.paused,
            "cancelled": self.cancelled,
        }


# Global state (initialized by create_app)
_config: ProtoNeoConfig | None = None
_llm_client: LLMClient | None = None
_session_manager: SessionManager | None = None
_engine: DeliberationEngine | None = None
_event_buses: dict[str, SessionEventBus] = {}
_pipeline_controls: dict[str, PipelineControl] = {}


def get_config() -> ProtoNeoConfig:
    return _config

def get_llm_client() -> LLMClient:
    return _llm_client

def get_session_manager() -> SessionManager:
    return _session_manager

def get_engine() -> DeliberationEngine:
    return _engine

def get_event_buses() -> dict[str, SessionEventBus]:
    return _event_buses

def get_pipeline_controls() -> dict[str, PipelineControl]:
    return _pipeline_controls


def _get_upload_dir() -> Path:
    d = Path(_config.storage.session_dir if _config else "data/sessions") / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Request/Response models

class CreateSessionRequest(BaseModel):
    agents: dict[str, AgentConfig] = Field(default_factory=dict)
    deliberation: DeliberationConfig = Field(default_factory=DeliberationConfig)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StartSessionRequest(BaseModel):
    message: str = Field(description="Initial prompt or instructions for the deliberation")


class SessionResponse(BaseModel):
    session_id: str
    status: str
    created_at: str
    result: dict[str, Any] | None = None
    error: str | None = None
    # Fix 15: Include config, paper stats, and pipeline progress
    config: dict[str, Any] | None = None
    paper_text_length: int = 0
    paper_markdown_length: int = 0
    current_stage: str = ""
    pipeline_steps: dict[str, Any] | None = None
    paper_graph_stats: dict[str, Any] | None = None


async def _run_with_events(
    session_id: str,
    agent_configs: dict[str, AgentConfig],
    delib_config: DeliberationConfig,
    user_message: str,
    bus: SessionEventBus,
) -> None:
    """Run deliberation and broadcast events through the session bus."""
    try:
        result = await _engine.run(
            session_id=session_id,
            agent_configs=agent_configs,
            deliberation_config=delib_config,
            user_message=user_message,
            on_event=lambda evt_type, data: bus.emit(evt_type, data),
        )
        bus.emit("completed", {"result": result.model_dump(mode="json")})
    except Exception as e:
        logger.error("Deliberation failed for session %s: %s", session_id, e)
        bus.emit("error", {"detail": str(e)})


async def _auto_discover_after_login():
    """Background task: re-run discovery after a successful OAuth login."""
    try:
        from ..llm.discovery import discover_all
        from ..llm.providers.registry import get_provider_registry
        from ..llm.settings import load_settings, save_settings

        settings = load_settings()
        oauth_registry = get_provider_registry()

        provider_credentials = {}
        for name in ["anthropic", "openai"]:
            info = oauth_registry.resolve_credential_info(name)
            if info.get("api_key"):
                provider_credentials[name] = info

        results = await discover_all(
            localhost_endpoints=[ep.model_dump() for ep in settings.localhost_endpoints],
            lan_endpoints=[ep.model_dump() for ep in settings.lan_endpoints],
            provider_credentials=provider_credentials,
            openrouter_free_only=settings.openrouter_free_only,
        )

        cached: dict[str, list[dict[str, Any]]] = {}
        for group_name in ("localhost", "lan"):
            nodes = results.get(group_name, [])
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                provider_name = str(node.get("id") or node.get("name") or "")
                if provider_name:
                    cached[provider_name] = [
                        {**m, "source": provider_name}
                        for m in node.get("models", [])
                        if isinstance(m, dict)
                    ]
        for key, val in results.items():
            if isinstance(val, dict) and "models" in val:
                cached[key] = val["models"]
        settings.discovered_models = cached
        save_settings(settings)
        if _llm_client is not None:
            _llm_client.registry = CapabilityRegistry.from_settings(settings)
        logger.info("Auto-discovery after login completed: %d providers", len(cached))
    except Exception as e:
        logger.warning("Auto-discovery after login failed: %s", e)


def register_kernel_routes(app: FastAPI, config: ProtoNeoConfig | None = None) -> None:
    """Register all kernel routes on the FastAPI app."""
    global _config, _llm_client, _session_manager, _engine

    from ..deliberation.session import BatchManager

    _config = config or ProtoNeoConfig.from_env()
    _llm_client = LLMClient.from_config(_config)
    _session_manager = SessionManager(_config.storage.session_dir)
    _engine = DeliberationEngine(_llm_client, _session_manager)

    # Also initialize batch manager for PC Panel
    _batch_manager = BatchManager(
        Path(_config.storage.session_dir).parent / "batches"
    )
    # Store batch_manager on app state for PC Panel to access
    app.state.batch_manager = _batch_manager

    # ── Health ──────────────────────────────────────────────

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "service": "ProtoNeo Kernel"}

    # ── Settings ─────────────────────────────────────────────

    @app.get("/api/settings")
    async def get_settings():
        """Get all ProtoNeo settings."""
        from ..llm.settings import load_settings
        return load_settings().model_dump()

    @app.put("/api/settings")
    async def put_settings(body: dict[str, Any]):
        """Update settings (partial merge)."""
        from ..llm.settings import update_settings
        updated = update_settings(body)
        if _llm_client is not None:
            _llm_client.registry = CapabilityRegistry.from_settings(updated)
        return updated.model_dump()

    @app.get("/api/settings/active-models")
    async def get_active_model_assignments():
        """Return ready-to-use active model routing for the panel pipeline."""
        from ..llm.providers.registry import get_provider_registry
        from ..llm.settings import active_model_assignments, load_settings

        return active_model_assignments(
            settings=load_settings(),
            provider_registry=get_provider_registry(),
        )

    # ── Presets ─────────────────────────────────────────────

    @app.get("/api/presets")
    async def list_presets():
        """List all available model assignment presets."""
        from ..llm.settings import get_all_presets, load_settings
        settings = load_settings()
        presets = get_all_presets(settings)
        return {
            "presets": [p.model_dump() for p in presets],
            "active_preset": settings.active_preset,
        }

    @app.post("/api/presets/{name}/activate")
    async def activate_preset(name: str):
        """Set the active preset and return its assignments."""
        from ..llm.settings import resolve_preset, load_settings, save_settings
        settings = load_settings()
        preset = resolve_preset(name, settings)
        if not preset:
            raise HTTPException(status_code=404, detail=f"Preset '{name}' not found")
        settings.active_preset = name
        save_settings(settings)
        return {"active_preset": name, "assignments": preset.assignments}

    # ── Models ──────────────────────────────────────────────

    @app.get("/api/models")
    async def list_models():
        """List registered models (from static registry, enriched by discovery)."""
        registry: CapabilityRegistry = _llm_client.registry
        return {
            "models": [
                {
                    "model_id": m.model_id,
                    "provider": m.provider,
                    "capabilities": sorted(c.value for c in m.capabilities),
                    "max_context": m.max_context,
                    "tier": m.tier.value,
                    "display_name": m.display_name or m.model_id,
                    "speed_tps": m.speed_tps,
                    "is_private": m.is_private,
                    "cost_per_input_token": m.cost_per_input_token,
                    "cost_per_output_token": m.cost_per_output_token,
                }
                for m in registry.list_all()
            ]
        }

    @app.post("/api/models/discover")
    async def discover_models():
        """Discover available models from all connected providers."""
        from ..llm.discovery import discover_all
        from ..llm.providers.registry import get_provider_registry
        from ..llm.settings import load_settings, save_settings

        settings = load_settings()
        oauth_registry = get_provider_registry()

        provider_credentials = {}
        for name in ["anthropic", "openai"]:
            info = oauth_registry.resolve_credential_info(name)
            if info.get("api_key"):
                provider_credentials[name] = info
        or_key = _llm_client._api_keys.get("openrouter") or os.getenv("OPENROUTER_API_KEY")
        if or_key:
            provider_credentials["openrouter"] = {
                "provider": "openrouter",
                "api_key": or_key,
                "api_key_source": "env" if os.getenv("OPENROUTER_API_KEY") else "config",
                "token_type": "api_key",
            }

        results = await discover_all(
            localhost_endpoints=[ep.model_dump() for ep in settings.localhost_endpoints],
            lan_endpoints=[ep.model_dump() for ep in settings.lan_endpoints],
            provider_credentials=provider_credentials,
            openrouter_free_only=settings.openrouter_free_only,
        )

        cached: dict[str, list[dict[str, Any]]] = {}
        for group_name in ("localhost", "lan"):
            nodes = results.get(group_name, [])
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if not isinstance(node, dict):
                    continue

                provider_name = str(node.get("id") or node.get("name") or "")
                if not provider_name:
                    continue

                models = []
                for model in node.get("models", []):
                    if not isinstance(model, dict):
                        continue
                    entry = dict(model)
                    entry["source"] = provider_name
                    models.append(entry)
                cached[provider_name] = models

                loaded_model = node.get("loaded_model")
                if loaded_model and not settings.active_models.get(provider_name):
                    settings.active_models[provider_name] = loaded_model

        for key, val in results.items():
            if isinstance(val, dict) and "models" in val:
                cached[key] = val["models"]
        settings.discovered_models = cached
        save_settings(settings)
        if _llm_client is not None:
            _llm_client.registry = CapabilityRegistry.from_settings(settings)

        return results

    # Per-session benchmark state
    _benchmark_results: dict[str, list] = {}
    _benchmark_running: set[str] = set()

    class BenchmarkRequest(BaseModel):
        model_ids: list[str] = Field(default_factory=list,
            description="Provider-prefixed model IDs to benchmark.")

    @app.post("/api/models/benchmark")
    async def start_benchmark(req: BenchmarkRequest):
        """Run a review-quality benchmark on active models."""
        from ..llm.settings import active_model_assignments, load_settings, save_settings
        from ..llm.providers.registry import get_provider_registry

        if "global" in _benchmark_running:
            raise HTTPException(status_code=409, detail="Benchmark already running")

        settings = load_settings()

        targets = []
        assignments = active_model_assignments(
            settings=settings,
            provider_registry=get_provider_registry(),
        )
        for provider, assignment in assignments.items():
            model_id = assignment["model_id"]
            target_id = f"{provider}/{model_id}"
            if req.model_ids and target_id not in req.model_ids and model_id not in req.model_ids:
                continue
            targets.append(
                {
                    "model_id": model_id,
                    "provider": provider,
                    "api_base": assignment.get("api_base", ""),
                    "litellm_model": assignment.get("litellm_model", ""),
                }
            )

        if not targets:
            raise HTTPException(status_code=400, detail="No active models to benchmark. Select models in the provider dropdowns first.")

        _benchmark_running.add("global")
        _benchmark_results["latest"] = []

        bus = SessionEventBus()
        _event_buses["benchmark"] = bus

        async def _run():
            from ..llm.benchmark import benchmark_all_parallel
            try:
                def on_progress(event, model_id, provider_or_dim=None, result=None):
                    if event == "start":
                        bus.emit("benchmark_progress", {
                            "model_id": model_id, "provider": provider_or_dim,
                            "status": "running", "total": len(targets),
                        })
                    elif event == "dimension":
                        bus.emit("benchmark_dimension", {
                            "model_id": model_id, "dimension": provider_or_dim,
                        })
                    elif event == "complete":
                        _benchmark_results.setdefault("latest", []).append(result)
                        bus.emit("benchmark_result", result)

                results = await benchmark_all_parallel(targets, _llm_client, on_progress)
                _benchmark_results["latest"] = results

                bus.emit("benchmark_complete", {
                    "total": len(targets),
                    "results": results,
                })
                settings_now = load_settings()
                existing_by_key = {
                    f"{r.get('provider','')}/{r.get('model_id','')}": r
                    for r in settings_now.benchmark_results
                    if isinstance(r, dict)
                }
                for r in results:
                    key = f"{r.get('provider','')}/{r.get('model_id','')}"
                    existing_by_key[key] = r
                settings_now.benchmark_results = list(existing_by_key.values())
                save_settings(settings_now)
                if _llm_client is not None:
                    _llm_client.registry = CapabilityRegistry.from_settings(settings_now)
            except Exception as e:
                bus.emit("benchmark_error", {"error": str(e)})
            finally:
                _benchmark_running.discard("global")

        asyncio.create_task(_run())
        return {
            "status": "started",
            "model_count": len(targets),
            "model_ids": [f"{t['provider']}/{t['model_id']}" for t in targets],
        }

    @app.get("/api/models/benchmark")
    async def get_benchmark_results():
        """Get the latest benchmark results."""
        from ..llm.settings import load_settings
        live = _benchmark_results.get("latest", [])
        stored = load_settings().benchmark_results
        return {
            "running": "global" in _benchmark_running,
            "results": live if live else stored,
        }

    # ── AI Providers (OAuth login for subscription services) ──

    @app.get("/api/providers")
    async def list_providers():
        """List all AI providers with connection status."""
        from ..llm.providers.registry import get_provider_registry
        from ..llm.settings import load_settings

        registry = get_provider_registry()
        settings = load_settings()

        providers = registry.all_status()

        or_key = _llm_client._api_keys.get("openrouter") or os.getenv("OPENROUTER_API_KEY")
        providers.append({
            "provider": "openrouter",
            "display_name": "OpenRouter",
            "logged_in": False,
            "has_credentials": bool(or_key),
            "type": "api_key",
            "api_key_source": "env" if os.getenv("OPENROUTER_API_KEY") else ("config" if or_key else "none"),
        })

        providers.append({
            "provider": "local",
            "display_name": "Local Runtime",
            "logged_in": False,
            "has_credentials": True,
            "type": "local",
            "nodes": [
                endpoint.model_dump()
                for endpoint in [*settings.localhost_endpoints, *settings.lan_endpoints]
            ],
        })

        return {"providers": providers}

    @app.get("/api/providers/{provider_name}")
    async def get_provider_status(provider_name: str):
        """Get detailed status for a single provider."""
        from ..llm.providers.registry import get_provider_registry
        registry = get_provider_registry()
        return registry.provider_status(provider_name)

    class LoginRequest(BaseModel):
        provider: str

    _pending_logins: dict[str, dict] = {}

    @app.post("/api/providers/login")
    async def begin_provider_login(req: LoginRequest):
        """Start OAuth login for a subscription provider."""
        from ..llm.providers.registry import get_provider_registry
        registry = get_provider_registry()
        try:
            auth_info = registry.begin_login(req.provider)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        provider = registry.get_provider(req.provider)
        login_state = {
            "verifier": auth_info["verifier"],
            "state": auth_info["state"],
            "status": "waiting",
            "result": None,
            "error": None,
        }

        if auth_info.get("needs_local_server"):
            port = auth_info.get("callback_port", 0)
            path = auth_info.get("callback_path", "/")
            login_state["auto_capture"] = True

            async def _run_callback_server():
                """Ephemeral HTTP server that captures one OAuth callback."""
                captured = asyncio.get_event_loop().create_future()

                class Handler(asyncio.Protocol):
                    """Minimal HTTP protocol that captures the OAuth code."""
                    def __init__(self):
                        self.transport = None
                        self.data = b""

                    def connection_made(self, transport):
                        self.transport = transport

                    def data_received(self, data):
                        self.data += data
                        if b"\r\n\r\n" in self.data:
                            request_line = self.data.split(b"\r\n")[0].decode()
                            if "code=" in request_line:
                                from urllib.parse import urlparse, parse_qs
                                url_part = request_line.split(" ")[1]
                                parsed = urlparse(url_part)
                                params = parse_qs(parsed.query)
                                code = params.get("code", [""])[0]
                                state = params.get("state", [""])[0]

                                body = (
                                    "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                                    "<h2>Login successful</h2>"
                                    "<p>You can close this tab and return to ProtoNeo.</p>"
                                    "<script>setTimeout(()=>window.close(),2000)</script>"
                                    "</body></html>"
                                )
                                resp = (
                                    f"HTTP/1.1 200 OK\r\n"
                                    f"Content-Type: text/html\r\n"
                                    f"Content-Length: {len(body)}\r\n"
                                    f"Connection: close\r\n\r\n"
                                    f"{body}"
                                )
                                self.transport.write(resp.encode())
                                self.transport.close()
                                if not captured.done():
                                    captured.set_result((code, state))
                            else:
                                self.transport.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
                                self.transport.close()

                try:
                    loop = asyncio.get_event_loop()
                    server = await loop.create_server(Handler, "0.0.0.0", port)
                    try:
                        code, state = await asyncio.wait_for(captured, timeout=300)
                        login_state["status"] = "captured"
                        login_state["result"] = {"code": code, "state": state}
                        try:
                            result = await registry.complete_login(
                                req.provider, code,
                                login_state["verifier"], state,
                            )
                            login_state["status"] = "complete"
                            login_state["result"] = result
                            asyncio.create_task(_auto_discover_after_login())
                        except Exception as e:
                            login_state["status"] = "error"
                            login_state["error"] = str(e)
                    except asyncio.TimeoutError:
                        login_state["status"] = "timeout"
                        login_state["error"] = "Login timed out after 5 minutes"
                    finally:
                        server.close()
                        await server.wait_closed()
                except OSError as e:
                    login_state["status"] = "error"
                    login_state["error"] = f"Could not start callback server on port {port}: {e}"

            asyncio.create_task(_run_callback_server())
        else:
            login_state["auto_capture"] = False

        _pending_logins[req.provider] = login_state
        return auth_info

    @app.get("/api/providers/{provider_name}/login-status")
    async def get_login_status(provider_name: str):
        """Poll for auto-capture login completion."""
        login_state = _pending_logins.get(provider_name)
        if not login_state:
            return {"status": "none", "message": "No pending login for this provider"}

        result = {
            "status": login_state["status"],
            "auto_capture": login_state.get("auto_capture", False),
        }
        if login_state["status"] == "complete":
            result["result"] = login_state["result"]
            _pending_logins.pop(provider_name, None)
        elif login_state["status"] == "error":
            result["error"] = login_state.get("error")
            _pending_logins.pop(provider_name, None)
        elif login_state["status"] == "timeout":
            _pending_logins.pop(provider_name, None)
        return result

    class LoginCallbackRequest(BaseModel):
        provider: str
        code: str
        verifier: str = ""
        state: str = ""

    @app.post("/api/providers/callback")
    async def complete_provider_login(req: LoginCallbackRequest):
        """Complete OAuth login via manual code paste."""
        from ..llm.providers.registry import get_provider_registry
        registry = get_provider_registry()
        provider = registry.get_provider(req.provider)
        if not provider:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")

        code, parsed_state = provider.parse_callback_input(req.code)

        pending = _pending_logins.get(req.provider, {})
        verifier = req.verifier or pending.get("verifier", "")
        state = parsed_state or req.state or pending.get("state", "")

        if not verifier:
            raise HTTPException(status_code=400, detail="No pending login. Call /api/providers/login first.")

        try:
            result = await registry.complete_login(req.provider, code, verifier, state)
            _pending_logins.pop(req.provider, None)
            asyncio.create_task(_auto_discover_after_login())
            return result
        except Exception as e:
            logger.error("OAuth login failed for %s: %s", req.provider, e)
            raise HTTPException(status_code=400, detail=f"Login failed: {e}")

    @app.post("/api/providers/{provider_name}/logout")
    async def provider_logout(provider_name: str):
        """Log out from a subscription provider."""
        from ..llm.providers.registry import get_provider_registry
        registry = get_provider_registry()
        registry.logout(provider_name)
        return {"provider": provider_name, "logged_in": False}

    # ── Sessions ────────────────────────────────────────────

    @app.post("/api/sessions", response_model=SessionResponse)
    async def create_session(req: CreateSessionRequest):
        session = await _session_manager.create(
            config={
                "agents": {k: v.model_dump() for k, v in req.agents.items()},
                "deliberation": req.deliberation.model_dump(),
                "metadata": req.metadata,
            }
        )
        return SessionResponse(
            session_id=session.session_id,
            status=session.status.value,
            created_at=session.created_at.isoformat(),
        )

    @app.get("/api/sessions/{session_id}", response_model=SessionResponse)
    async def get_session(session_id: str):
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        # Fix 15: Compute graph summary stats if paper_graph is present
        graph_stats = None
        if session.paper_graph:
            graph_stats = {
                "node_count": len(session.paper_graph.get("nodes", [])),
                "edge_count": len(session.paper_graph.get("edges", [])),
                "has_summary": bool(session.paper_graph.get("summary")),
            }
        return SessionResponse(
            session_id=session.session_id,
            status=session.status.value,
            created_at=session.created_at.isoformat(),
            result=session.result,
            error=session.error,
            config=session.config or None,
            paper_text_length=len(session.paper_text),
            paper_markdown_length=len(session.paper_markdown),
            current_stage=session.current_stage,
            pipeline_steps=session.pipeline_steps or None,
            paper_graph_stats=graph_stats,
        )

    @app.get("/api/sessions")
    async def list_sessions(limit: int = 50):
        sessions = await _session_manager.list_sessions(limit=limit)
        items = []
        for s in sessions:
            graph_stats = None
            if s.paper_graph:
                graph_stats = {
                    "node_count": len(s.paper_graph.get("nodes", [])),
                    "edge_count": len(s.paper_graph.get("edges", [])),
                    "has_summary": bool(s.paper_graph.get("summary")),
                }
            items.append(SessionResponse(
                session_id=s.session_id,
                status=s.status.value,
                created_at=s.created_at.isoformat(),
                result=s.result,
                error=s.error,
                config=s.config or None,
                paper_text_length=len(s.paper_text),
                paper_markdown_length=len(s.paper_markdown),
                current_stage=s.current_stage,
                pipeline_steps=s.pipeline_steps or None,
                paper_graph_stats=graph_stats,
            ))
        return {"sessions": items}

    @app.post("/api/sessions/{session_id}/upload")
    async def upload_document(session_id: str, file: UploadFile = File(...)):
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        upload_dir = _get_upload_dir()
        safe_name = f"{uuid.uuid4().hex}_{file.filename}"
        file_path = upload_dir / safe_name

        content = await file.read()
        file_path.write_bytes(content)

        try:
            doc = parse_file(str(file_path))
            doc = chunk_document(doc)
        except Exception as e:
            file_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=str(e))

        context = _session_manager.get_context(session_id)
        context.add_document(doc)

        session.document_ids.append(doc.document_id)
        await _session_manager.update(session)

        return {
            "document_id": doc.document_id,
            "filename": doc.filename,
            "chunks": len(doc.chunks),
            "text_length": len(doc.text),
        }

    @app.post("/api/sessions/{session_id}/start")
    async def start_session(session_id: str, req: StartSessionRequest):
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.status == SessionStatus.RUNNING:
            raise HTTPException(status_code=409, detail="Session already running")

        cfg = session.config
        agent_configs = {
            k: AgentConfig(**v) for k, v in cfg.get("agents", {}).items()
        }
        delib_config = DeliberationConfig(**cfg.get("deliberation", {}))

        if not agent_configs:
            raise HTTPException(status_code=400, detail="No agents configured")

        bus = SessionEventBus()
        _event_buses[session_id] = bus
        asyncio.create_task(
            _run_with_events(session_id, agent_configs, delib_config, req.message, bus)
        )

        return {"session_id": session_id, "status": "running"}

    @app.post("/api/sessions/{session_id}/stop")
    async def stop_session(session_id: str):
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        session.status = SessionStatus.STOPPED
        await _session_manager.update(session)
        return {"session_id": session_id, "status": "stopped"}

    # ── WebSocket streaming ─────────────────────────────────

    @app.websocket("/api/sessions/{session_id}/stream")
    async def stream_session(websocket: WebSocket, session_id: str):
        await websocket.accept()
        session = await _session_manager.get(session_id)
        if not session:
            await websocket.close(code=4004, reason="Session not found")
            return

        bus = _event_buses.get(session_id)

        if not bus and session.status in (SessionStatus.FAILED, "failed"):
            await websocket.send_json({"type": "error", "detail": session.error or "Unknown error"})
            await websocket.close()
            return

        # Always ensure a bus exists so post-review chat can emit events.
        if not bus:
            bus = SessionEventBus()
            _event_buses[session_id] = bus

        # For already-completed sessions, send the terminal event immediately
        # but keep the connection open for post-review chat streaming.
        if session.status in (SessionStatus.COMPLETED, "completed"):
            await websocket.send_json({"type": "completed", "result": session.result})

        queue = bus.subscribe()

        async def read_client():
            try:
                while True:
                    raw = await websocket.receive_text()
                    msg = json.loads(raw)
                    if msg.get("action") == "start":
                        current = await _session_manager.get(session_id)
                        if current and current.status not in (
                            SessionStatus.RUNNING, "running",
                            SessionStatus.COMPLETED, "completed",
                        ):
                            cfg = current.config
                            ac = {k: AgentConfig(**v) for k, v in cfg.get("agents", {}).items()}
                            dc = DeliberationConfig(**cfg.get("deliberation", {}))
                            asyncio.create_task(
                                _run_with_events(session_id, ac, dc, msg.get("message", ""), bus)
                            )
            except (WebSocketDisconnect, Exception):
                pass

        read_task = asyncio.create_task(read_client())

        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
                # Only terminate on unrecoverable errors.
                # Keep the connection alive after "completed" so
                # post-review chat tokens can stream through.
                if event["type"] == "error":
                    break
        except WebSocketDisconnect:
            logger.info("WebSocket disconnected for session %s", session_id)
        except Exception as e:
            logger.error("WebSocket error for session %s: %s", session_id, e)
            try:
                await websocket.close(code=1011, reason=str(e))
            except Exception:
                pass
        finally:
            bus.unsubscribe(queue)
            read_task.cancel()

    # ── Agents ──────────────────────────────────────────────

    @app.get("/api/agents")
    async def list_agents():
        """List agent configurations from the running config."""
        agents = _config.agents if _config else {}
        return {
            "agents": [
                {
                    "agent_id": aid,
                    "role": cfg.role,
                    "model": cfg.model,
                    "focus": cfg.focus,
                }
                for aid, cfg in agents.items()
            ]
        }
