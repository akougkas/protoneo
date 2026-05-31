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

from ..config.schema import AgentConfig, AppManifest, DeliberationConfig, ProtoNeoConfig
from ..deliberation.engine import DeliberationEngine
from ..deliberation.session import SessionManager, SessionStatus, StepState
from ..knowledge.chunker import chunk_document
from ..knowledge.graph import KnowledgeGraph
from ..knowledge.graph_extractor import extract_graph
from ..knowledge.ontology import generate_ontology as _generate_ontology
from ..knowledge.parser import parse_file
from ..llm.client import LLMClient
from ..llm.registry import CapabilityRegistry
from ..llm.settings import build_vlm_config
from .events import SessionEventBus
from .pipeline_control import PipelineControl

logger = logging.getLogger("protoneo.api")


__all__ = [
    "SessionEventBus",
    "PipelineControl",
    "register_kernel_routes",
    "set_registries",
    "get_config",
    "get_llm_client",
    "get_session_manager",
    "get_engine",
    "get_event_buses",
    "get_pipeline_controls",
    "get_session_graphs",
    "get_session_ontologies",
    "get_batch_manager",
    "get_manifests",
    "_get_upload_dir",
]


# Global state (initialized by create_app)
_config: ProtoNeoConfig | None = None
_llm_client: LLMClient | None = None
_session_manager: SessionManager | None = None
_engine: DeliberationEngine | None = None
_event_buses: dict[str, SessionEventBus] = {}
_pipeline_controls: dict[str, PipelineControl] = {}

# Knowledge caches (in-memory, populated during pipeline execution)
_session_graphs: dict[str, dict] = {}
_session_ontologies: dict[str, Any] = {}
_batch_manager: Any = None

# Registry references (set by create_app via set_registries)
_manifests: dict[str, AppManifest] = {}
_doc_processor: Any = None
_tool_registry: Any = None
_export_registry: Any = None


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

def get_session_graphs() -> dict[str, dict]:
    return _session_graphs

def get_session_ontologies() -> dict[str, Any]:
    return _session_ontologies

def get_batch_manager():
    return _batch_manager

def get_manifests() -> dict[str, AppManifest]:
    return _manifests

def set_registries(manifests, doc_processor, tool_registry, export_registry):
    """Called by create_app to share registry references with kernel routes."""
    global _manifests, _doc_processor, _tool_registry, _export_registry
    _manifests = manifests
    _doc_processor = doc_processor
    _tool_registry = tool_registry
    _export_registry = export_registry


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
    config: dict[str, Any] | None = None
    document_text_length: int = 0
    document_markdown_length: int = 0
    current_stage: str = ""
    pipeline_steps: dict[str, Any] | None = None
    knowledge_graph_stats: dict[str, Any] | None = None


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
        from ..llm.errors import sanitize_error_message

        error = sanitize_error_message(e)
        logger.error("Deliberation failed for session %s: %s", session_id, error)
        bus.emit("error", {"detail": error})


async def _auto_discover_after_login():
    """Background task: re-run discovery after a successful OAuth login."""
    try:
        from ..llm.discovery import discover_all
        from ..llm.settings import load_settings, save_settings

        settings = load_settings()

        results = await discover_all(
            localhost_endpoints=[ep.model_dump() for ep in settings.localhost_endpoints],
            lan_endpoints=[ep.model_dump() for ep in settings.lan_endpoints],
            provider_credentials=_provider_credentials(),
            openrouter_free_only=settings.openrouter_free_only,
            cached_models=settings.discovered_models,
            force_refresh=True,
        )

        cached, _ = _discovery_cache_updates(results, settings.discovered_models)
        settings.discovered_models = {**settings.discovered_models, **cached}
        save_settings(settings)
        if _llm_client is not None:
            _llm_client.registry = CapabilityRegistry.from_settings(settings)
        logger.info("Auto-discovery after login completed: %d providers", len(cached))
    except Exception as e:
        logger.warning("Auto-discovery after login failed: %s", e)


def _openrouter_key() -> str | None:
    return (
        (_llm_client._api_keys.get("openrouter") if _llm_client is not None else None)
        or os.getenv("OPENROUTER_API_KEY")
    )


def _provider_credentials() -> dict[str, dict[str, Any]]:
    from ..llm.providers.registry import get_provider_registry

    oauth_registry = get_provider_registry()
    provider_credentials: dict[str, dict[str, Any]] = {}
    for name in ["openai"]:
        info = oauth_registry.resolve_credential_info(name)
        if info.get("api_key"):
            provider_credentials[name] = info

    or_key = _openrouter_key()
    if or_key:
        provider_credentials["openrouter"] = {
            "provider": "openrouter",
            "api_key": or_key,
            "api_key_source": "env" if os.getenv("OPENROUTER_API_KEY") else "config",
            "token_type": "api_key",
        }
    return provider_credentials


def _discovery_cache_updates(
    results: dict[str, Any],
    previous_cache: dict[str, list[dict[str, Any]]] | None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, bool]]:
    from ..llm.model_catalog import cache_from_discovery_results

    return cache_from_discovery_results(results, previous_cache)


def register_kernel_routes(app: FastAPI, config: ProtoNeoConfig | None = None) -> None:
    """Register all kernel routes on the FastAPI app."""
    global _config, _llm_client, _session_manager, _engine

    from ..deliberation.session import BatchManager

    _config = config or ProtoNeoConfig.from_env()
    _llm_client = LLMClient.from_config(_config)
    _session_manager = SessionManager(_config.storage.session_dir)
    _engine = DeliberationEngine(_llm_client, _session_manager)

    # Initialize batch manager (shared with app routes via get_batch_manager)
    global _batch_manager
    _batch_manager = BatchManager(
        Path(_config.storage.session_dir).parent / "batches"
    )

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
        """List the normalized runtime model catalog."""
        from ..llm.model_catalog import build_model_catalog
        from ..llm.settings import load_settings

        settings = load_settings()
        registry: CapabilityRegistry = _llm_client.registry
        return {
            "models": build_model_catalog(settings, registry),
            "active_models": settings.active_models,
            "active_model_options": settings.active_model_options,
        }

    @app.get("/api/model-policies")
    async def list_model_policies():
        """Return phase policy metadata for UI warnings and routing explanations."""
        from ..llm.policies import phase_policy_metadata

        return {"policies": phase_policy_metadata()}

    @app.post("/api/models/discover")
    async def discover_models():
        """Discover available models from all connected providers."""
        from ..llm.discovery import discover_all
        from ..llm.model_catalog import build_model_catalog
        from ..llm.settings import load_settings, save_settings

        settings = load_settings()

        provider_credentials = _provider_credentials()
        if provider_credentials.get("openrouter"):
            settings.provider_enabled.setdefault("openrouter", True)
        if provider_credentials.get("openai"):
            settings.provider_enabled.setdefault("openai", True)

        results = await discover_all(
            localhost_endpoints=[ep.model_dump() for ep in settings.localhost_endpoints],
            lan_endpoints=[ep.model_dump() for ep in settings.lan_endpoints],
            provider_credentials=provider_credentials,
            openrouter_free_only=settings.openrouter_free_only,
            cached_models=settings.discovered_models,
            force_refresh=True,
        )

        cached, live_success = _discovery_cache_updates(results, settings.discovered_models)
        settings.discovered_models = {**settings.discovered_models, **cached}
        for group_name in ("localhost", "lan"):
            nodes = results.get(group_name, [])
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                provider_name = str(node.get("id") or node.get("name") or "")
                loaded_model = node.get("loaded_model")
                if loaded_model and live_success.get(provider_name) and not settings.active_models.get(provider_name):
                    settings.active_models[provider_name] = loaded_model

        save_settings(settings)
        if _llm_client is not None:
            _llm_client.registry = CapabilityRegistry.from_settings(settings)
            results["catalog"] = build_model_catalog(settings, _llm_client.registry)

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
                try:
                    from ..llm.discovery import discover_local

                    local_updates: dict[str, Any] = {}
                    if settings_now.localhost_endpoints:
                        local_updates["localhost"] = await discover_local(
                            [ep.model_dump() for ep in settings_now.localhost_endpoints]
                        )
                    if settings_now.lan_endpoints:
                        local_updates["lan"] = await discover_local(
                            [ep.model_dump() for ep in settings_now.lan_endpoints]
                        )
                    if local_updates:
                        cached, _ = _discovery_cache_updates(
                            local_updates,
                            settings_now.discovered_models,
                        )
                        settings_now.discovered_models = {
                            **settings_now.discovered_models,
                            **cached,
                        }
                except Exception as e:
                    logger.warning("Post-benchmark local discovery refresh failed: %s", e)

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
        running = "global" in _benchmark_running
        return {
            "running": running,
            "results": live if (running or live) else stored,
        }

    # ── AI Providers (OAuth login for subscription services) ──

    @app.get("/api/providers")
    async def list_providers():
        """List all first-class AI providers with configuration status."""
        from ..llm.providers.registry import get_provider_registry
        from ..llm.settings import all_configured_endpoints, load_settings, provider_is_enabled

        registry = get_provider_registry()
        settings = load_settings()
        provider_rows: list[dict[str, Any]] = []

        for endpoint in all_configured_endpoints(settings):
            cached = settings.discovered_models.get(endpoint.id, [])
            live_cached = any(
                isinstance(model, dict) and model.get("discovery_source") == "live"
                for model in cached
            )
            provider_rows.append({
                "provider_id": endpoint.id,
                "provider": endpoint.id,
                "display_name": endpoint.display_name,
                "kind": endpoint.location,
                "type": endpoint.type,
                "enabled": endpoint.enabled,
                "editable_endpoint": True,
                "endpoint": endpoint.model_dump(),
                "has_credentials": True,
                "api_key_source": "local",
                "online": bool(cached) and live_cached,
                "model_count": len(cached),
                "active_model": settings.active_models.get(endpoint.id, ""),
                "active_model_options": settings.active_model_options.get(endpoint.id, {}),
            })

        or_key = _openrouter_key()
        provider_rows.append({
            "provider_id": "openrouter",
            "provider": "openrouter",
            "display_name": "OpenRouter",
            "kind": "api",
            "type": "openrouter",
            "enabled": provider_is_enabled("openrouter", settings),
            "editable_endpoint": False,
            "openrouter_free_only": settings.openrouter_free_only,
            "has_credentials": bool(or_key),
            "api_key_source": "env" if os.getenv("OPENROUTER_API_KEY") else ("config" if or_key else "none"),
            "logged_in": False,
            "online": bool(settings.discovered_models.get("openrouter")),
            "model_count": len(settings.discovered_models.get("openrouter", [])),
            "active_model": settings.active_models.get("openrouter", ""),
            "active_model_options": settings.active_model_options.get("openrouter", {}),
        })

        openai_status = registry.provider_status("openai")
        provider_rows.append({
            **openai_status,
            "provider_id": "openai",
            "provider": "openai",
            "display_name": openai_status.get("display_name") or "ChatGPT/OpenAI",
            "kind": "subscription",
            "type": "openai",
            "enabled": provider_is_enabled("openai", settings),
            "editable_endpoint": False,
            "online": bool(settings.discovered_models.get("openai")),
            "model_count": len(settings.discovered_models.get("openai", [])),
            "active_model": settings.active_models.get("openai", ""),
            "active_model_options": settings.active_model_options.get("openai", {}),
        })

        return {"providers": provider_rows}

    @app.get("/api/providers/{provider_name}")
    async def get_provider_status(provider_name: str):
        """Get detailed status for a single provider."""
        from ..llm.providers.registry import get_provider_registry
        from ..llm.settings import endpoint_map, load_settings, provider_is_enabled

        settings = load_settings()
        endpoints = endpoint_map(settings)
        if provider_name in endpoints:
            endpoint = endpoints[provider_name]
            return {
                "provider_id": endpoint.id,
                "provider": endpoint.id,
                "display_name": endpoint.display_name,
                "kind": endpoint.location,
                "type": endpoint.type,
                "enabled": endpoint.enabled,
                "endpoint": endpoint.model_dump(),
                "has_credentials": True,
                "api_key_source": "local",
                "model_count": len(settings.discovered_models.get(endpoint.id, [])),
                "active_model": settings.active_models.get(endpoint.id, ""),
                "active_model_options": settings.active_model_options.get(endpoint.id, {}),
            }

        if provider_name == "openrouter":
            or_key = _openrouter_key()
            return {
                "provider_id": "openrouter",
                "provider": "openrouter",
                "display_name": "OpenRouter",
                "kind": "api",
                "type": "openrouter",
                "enabled": provider_is_enabled("openrouter", settings),
                "has_credentials": bool(or_key),
                "api_key_source": "env" if os.getenv("OPENROUTER_API_KEY") else ("config" if or_key else "none"),
                "token_type": "api_key" if or_key else "",
                "openrouter_free_only": settings.openrouter_free_only,
                "model_count": len(settings.discovered_models.get("openrouter", [])),
                "active_model": settings.active_models.get("openrouter", ""),
                "active_model_options": settings.active_model_options.get("openrouter", {}),
            }
        registry = get_provider_registry()
        status = registry.provider_status(provider_name)
        if provider_name == "openai":
            status.update({
                "provider_id": "openai",
                "enabled": provider_is_enabled("openai", settings),
                "model_count": len(settings.discovered_models.get("openai", [])),
                "active_model": settings.active_models.get("openai", ""),
                "active_model_options": settings.active_model_options.get("openai", {}),
            })
        return status

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
        graph_stats = None
        if session.knowledge_graph:
            graph_stats = {
                "node_count": len(session.knowledge_graph.get("nodes", [])),
                "edge_count": len(session.knowledge_graph.get("edges", [])),
                "has_summary": bool(session.knowledge_graph.get("summary")),
            }
        return SessionResponse(
            session_id=session.session_id,
            status=session.status.value,
            created_at=session.created_at.isoformat(),
            result=session.result,
            error=session.error,
            config=session.config or None,
            document_text_length=len(session.document_text),
            document_markdown_length=len(session.document_markdown),
            current_stage=session.current_stage,
            pipeline_steps=session.pipeline_steps or None,
            knowledge_graph_stats=graph_stats,
        )

    @app.get("/api/sessions")
    async def list_sessions(limit: int = 50):
        sessions = await _session_manager.list_sessions(limit=limit)
        items = []
        for s in sessions:
            graph_stats = None
            if s.knowledge_graph:
                graph_stats = {
                    "node_count": len(s.knowledge_graph.get("nodes", [])),
                    "edge_count": len(s.knowledge_graph.get("edges", [])),
                    "has_summary": bool(s.knowledge_graph.get("summary")),
                }
            items.append(SessionResponse(
                session_id=s.session_id,
                status=s.status.value,
                created_at=s.created_at.isoformat(),
                result=s.result,
                error=s.error,
                config=s.config or None,
                document_text_length=len(s.document_text),
                document_markdown_length=len(s.document_markdown),
                current_stage=s.current_stage,
                pipeline_steps=s.pipeline_steps or None,
                knowledge_graph_stats=graph_stats,
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
            doc = parse_file(str(file_path), vlm_config=build_vlm_config())
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

        if not bus and session.status == SessionStatus.FAILED:
            await websocket.send_json({"type": "error", "detail": session.error or "Unknown error"})
            await websocket.close()
            return

        # Always ensure a bus exists so post-review chat can emit events.
        if not bus:
            bus = SessionEventBus()
            _event_buses[session_id] = bus

        # For already-completed sessions, send the terminal event immediately
        # but keep the connection open for post-review chat streaming.
        if session.status == SessionStatus.COMPLETED:
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
                            SessionStatus.RUNNING,
                            SessionStatus.COMPLETED,
                        ):
                            cfg = current.config
                            ac = {k: AgentConfig(**v) for k, v in cfg.get("agents", {}).items()}
                            dc = DeliberationConfig(**cfg.get("deliberation", {}))
                            asyncio.create_task(
                                _run_with_events(session_id, ac, dc, msg.get("message", ""), bus)
                            )
            except Exception:
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

    # ── Graph ──────────────────────────────────────────────

    @app.get("/api/sessions/{session_id}/graph")
    async def get_session_graph(session_id: str):
        """Get the knowledge graph for a session."""
        session = await _session_manager.get(session_id)
        if session and session.knowledge_graph:
            try:
                pg = KnowledgeGraph.model_validate(session.knowledge_graph)
                d3 = pg.to_d3_format()
                d3["stats"] = pg.graph_stats()
                d3["grounding"] = pg.grounding_summary()
                return d3
            except Exception:
                pass

        if session_id in _session_graphs:
            cached = dict(_session_graphs[session_id])
            if "grounding" not in cached:
                try:
                    pg = KnowledgeGraph()
                    pg.ingest_d3_data(cached)
                    cached["grounding"] = pg.grounding_summary()
                except Exception:
                    cached["grounding"] = {
                        "grounding_mode": "none",
                        "visual_evidence_count": 0,
                        "described_artifact_count": 0,
                        "undescribed_artifact_count": 0,
                    }
            return cached

        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        context = _session_manager.get_context(session_id)
        if not context.documents:
            return {"nodes": [], "edges": []}

        from ..knowledge.metadata import extract_metadata
        doc = context.documents[0]
        metadata = extract_metadata(doc.text)
        pg = KnowledgeGraph()
        pg.ingest_metadata(metadata)
        d3 = pg.to_d3_format()
        d3["grounding"] = pg.grounding_summary()
        return d3

    @app.get("/api/sessions/{session_id}/graph/step/{step_name}")
    async def get_graph_at_step(session_id: str, step_name: str):
        """Get the graph snapshot from after a specific pipeline step."""
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        snapshot = session.graph_after_step.get(step_name)
        if not snapshot:
            raise HTTPException(status_code=404, detail=f"No graph snapshot for step '{step_name}'")

        try:
            pg = KnowledgeGraph.restore_from_snapshot(snapshot)
            return {
                **pg.to_d3_format(),
                "stats": pg.graph_stats(),
                "grounding": pg.grounding_summary(),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to restore graph: {e}")

    @app.get("/api/sessions/{session_id}/graph/export")
    async def export_graph(session_id: str):
        """Export the session's KnowledgeGraph as a standalone JSON file."""
        from fastapi.responses import Response
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if not session.knowledge_graph:
            raise HTTPException(status_code=404, detail="No graph to export")

        export_data = {
            "schema_version": 1,
            "session_id": session_id,
            "paper_title": session.config.get("metadata", {}).get("paper_title", ""),
            "conference": session.config.get("metadata", {}).get("conference", ""),
            "artifact_description_status": session.config.get("metadata", {}).get("artifact_description_status", ""),
            "artifact_description_assumed_present": session.config.get("metadata", {}).get("artifact_description_assumed_present", False),
            "graph": session.knowledge_graph,
            "document_markdown": session.document_markdown or session.document_text,
        }

        filename = f"graph-{session_id[:8]}.json"
        return Response(
            content=json.dumps(export_data, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/sessions/{session_id}/ontology")
    async def get_session_ontology(session_id: str):
        """Get the generated ontology for a session."""
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

    @app.post("/api/sessions/{session_id}/generate-ontology")
    async def generate_ontology_endpoint(
        session_id: str,
        model: str = Form(""),
        conference_context: str = Form(""),
    ):
        """Generate a domain-specific ontology for the session's document."""
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
                "phase_num": 0, "total_phases": 3,
                "message": "Analyzing document domain and designing ontology...",
                "node_count": 0, "edge_count": 0,
            })

        # Resolve domain config from the session's owning app manifest
        domain_config = None
        if session.app_name and session.app_name in _manifests:
            domain_config = _manifests[session.app_name].domain_config

        doc = context.documents[0]
        ontology = await _generate_ontology(
            doc.text, _llm_client, model=model,
            session_id=session_id, conference_context=conference_context,
            domain_config=domain_config,
        )

        _session_ontologies[session_id] = ontology

        if bus:
            bus.emit("graph_progress", {
                "phase": "ontology_complete",
                "phase_num": 0, "total_phases": 3,
                "message": f"Ontology ready: {len(ontology.entity_types)} entity types, {len(ontology.edge_types)} relationship types",
                "node_count": 0, "edge_count": 0,
            })

        return {
            "session_id": session_id,
            "entity_types": [et.model_dump() for et in ontology.entity_types],
            "edge_types": [rt.model_dump() for rt in ontology.edge_types],
            "analysis_summary": ontology.analysis_summary,
            "paper_domain": ontology.paper_domain,
            "key_contributions": ontology.key_contributions,
        }

    @app.post("/api/sessions/{session_id}/extract-graph")
    async def extract_graph_endpoint(
        session_id: str,
        model: str = Form(""),
    ):
        """Extract a knowledge graph from the session's uploaded document."""
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
        graph_data = await extract_graph(
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

    @app.get("/api/sessions/{session_id}/graph-utilization")
    async def get_graph_utilization(session_id: str):
        """Compute how well agents utilized the knowledge graph."""
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if not session.knowledge_graph:
            raise HTTPException(status_code=404, detail="No graph for this session")
        if not session.result:
            raise HTTPException(status_code=409, detail="Session has no results yet")

        pg = KnowledgeGraph.model_validate(session.knowledge_graph)
        agent_outputs = []
        phases = session.result.get("phases", [])
        for phase in phases:
            if phase.get("phase_name") != "independent_review":
                continue
            for output in phase.get("outputs", []):
                parsed = output.get("structured", {})
                if not parsed:
                    try:
                        parsed = json.loads(output.get("content", ""))
                    except Exception:
                        parsed = {}
                if parsed:
                    parsed["agent_id"] = output.get("agent_id", "")
                    agent_outputs.append(parsed)

        return pg.compute_utilization(agent_outputs)

    @app.get("/api/sessions/{session_id}/graph-summary")
    async def get_graph_summary(session_id: str):
        """Get the agent briefing text and graph stats."""
        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if not session.knowledge_graph:
            raise HTTPException(status_code=404, detail="No graph built yet")

        try:
            pg = KnowledgeGraph.model_validate(session.knowledge_graph)
            return {
                "summary": pg.to_agent_briefing(),
                "stats": pg.graph_stats(),
                "grounding": pg.grounding_summary(),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to generate summary: {e}")

    # ── Pipeline Control ───────────────────────────────────

    @app.get("/api/sessions/{session_id}/pipeline")
    async def get_pipeline_status(session_id: str):
        """Get current pipeline state for a session."""
        ctl = _pipeline_controls.get(session_id)
        if not ctl:
            return {"session_id": session_id, "active": False}
        return {"session_id": session_id, "active": True, **ctl.status()}

    @app.get("/api/sessions/{session_id}/pipeline/status")
    async def get_all_pipeline_steps(session_id: str):
        """Get all step states for a session."""
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

    @app.post("/api/sessions/{session_id}/pipeline/advance")
    async def pipeline_advance(session_id: str):
        """Advance past the current gate."""
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
        """Cancel the entire pipeline."""
        ctl = _pipeline_controls.get(session_id)
        bus = _event_buses.get(session_id)

        if ctl:
            ctl.cancel()

        session = await _session_manager.get(session_id)
        if session:
            session.status = SessionStatus.STOPPED
            await _session_manager.update(session)

        if bus:
            bus.emit("pipeline_cancelled", {"message": "Pipeline cancelled"})
            bus.emit("error", {"detail": "Pipeline cancelled"})

        return {"session_id": session_id, "status": "cancelled"}

    class OntologyEdit(BaseModel):
        edited_entity_types: list[dict] | None = None
        edited_edge_types: list[dict] | None = None

    @app.post("/api/sessions/{session_id}/pipeline/edit-ontology")
    async def pipeline_edit_ontology(session_id: str, body: OntologyEdit):
        """Edit the generated ontology before advancing past it."""
        from ..knowledge.ontology import EntityType, EdgeType

        ontology = _session_ontologies.get(session_id)
        if not ontology:
            raise HTTPException(status_code=404, detail="No ontology for this session")

        if body.edited_entity_types is not None:
            ontology.entity_types = [EntityType(**et) for et in body.edited_entity_types]
        if body.edited_edge_types is not None:
            ontology.edge_types = [EdgeType(**rt) for rt in body.edited_edge_types]
        _session_ontologies[session_id] = ontology

        bus = _event_buses.get(session_id)
        if bus:
            bus.emit("ontology_edited", {
                "entity_types": len(ontology.entity_types),
                "edge_types": len(ontology.edge_types),
                "message": "Ontology edited",
            })
        return {
            "session_id": session_id,
            "entity_types": len(ontology.entity_types),
            "edge_types": len(ontology.edge_types),
        }

    @app.post("/api/sessions/{session_id}/pipeline/step/{step_name}/run")
    async def run_pipeline_step(session_id: str, step_name: str):
        """Run or re-run a single pipeline step."""
        import time as _time

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
        kg = KnowledgeGraph()
        if step_idx > 0:
            prev_step = valid_steps[step_idx - 1]
            prev_snapshot = session.graph_after_step.get(prev_step)
            if prev_snapshot:
                kg = KnowledgeGraph.restore_from_snapshot(prev_snapshot)
            elif session.knowledge_graph:
                kg = KnowledgeGraph.model_validate(session.knowledge_graph)

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

    # ── Export ─────────────────────────────────────────────

    @app.get("/api/export/formats")
    async def list_export_formats():
        """List available export formats."""
        if _export_registry:
            return {"formats": _export_registry.available_formats()}
        return {"formats": []}

    @app.get("/api/sessions/{session_id}/export")
    async def export_session(session_id: str, format: str = "json"):
        """Export session results in the requested format."""
        from fastapi.responses import Response

        if not _export_registry:
            raise HTTPException(status_code=500, detail="Export registry not initialized")

        session = await _session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        exporter = _export_registry.get(format, app_name=session.app_name)
        if not exporter:
            available = [f["format_name"] for f in _export_registry.available_formats(app_name=session.app_name)]
            raise HTTPException(
                status_code=400,
                detail=f"Unknown format '{format}'. Available: {available}",
            )

        app_data = session.app_data if session.app_data else None
        data = await exporter.export(session, app_data=app_data)

        filename = f"session-{session_id[:8]}.{exporter.file_extension}"
        return Response(
            content=data,
            media_type=exporter.mime_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ── Parsers ────────────────────────────────────────────

    @app.get("/api/parsers")
    async def list_parsers():
        """List available document parsers by extension."""
        if not _doc_processor:
            return {"parsers": {}}
        result = {}
        for ext in (".pdf", ".txt", ".text", ".md", ".markdown"):
            available = _doc_processor.available_parsers(ext)
            if available:
                result[ext] = available
        return {"parsers": result}

    # ── Tools ──────────────────────────────────────────────

    @app.get("/api/tools")
    async def list_tools():
        """List available tools."""
        if _tool_registry:
            return {"tools": _tool_registry.available_tools()}
        return {"tools": []}

    # ── Manifests ──────────────────────────────────────────

    @app.get("/api/manifests")
    async def list_manifests():
        """List all registered application manifests."""
        return {
            "apps": [
                {
                    "name": m.name,
                    "display_name": m.display_name,
                    "version": m.version,
                    "description": m.description,
                    "score_fields": m.score_fields,
                    "pipeline_stages": m.pipeline_stages,
                }
                for m in _manifests.values()
            ]
        }

    @app.get("/api/manifests/{app_name}")
    async def get_manifest(app_name: str):
        """Get a specific application manifest."""
        m = _manifests.get(app_name)
        if not m:
            raise HTTPException(status_code=404, detail=f"App '{app_name}' not registered")
        return {
            "name": m.name,
            "display_name": m.display_name,
            "version": m.version,
            "description": m.description,
            "score_fields": m.score_fields,
            "pipeline_stages": m.pipeline_stages,
        }
