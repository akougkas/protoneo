"""
ProtoNeo application factory.

Creates the FastAPI app, initializes kernel subsystem registries,
registers kernel routes, and mounts application routes.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..config.schema import AppManifest, AppRegistration, ProtoNeoConfig
from ..export import create_export_registry
from ..knowledge import create_document_processor
from ..tools import create_tool_registry
from .events import SessionEventBus as SessionEventBus
from .pipeline_control import PipelineControl as PipelineControl
from .routes import register_kernel_routes, set_registries


def create_app(
    config: ProtoNeoConfig | None = None,
    apps: list[AppManifest] | None = None,
) -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(title="ProtoNeo", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Initialize kernel subsystem registries
    doc_processor = create_document_processor()
    tool_registry = create_tool_registry()
    export_registry = create_export_registry()
    manifests: dict[str, AppManifest] = {}

    # Share registries with kernel routes module
    set_registries(manifests, doc_processor, tool_registry, export_registry)

    # Store on app.state for reference
    app.state.document_processor = doc_processor
    app.state.tool_registry = tool_registry
    app.state.export_registry = export_registry
    app.state.manifests = manifests

    # Register kernel routes (health, settings, models, sessions, graph, pipeline, etc.)
    register_kernel_routes(app, config)

    # Register applications through manifest interface
    for manifest in (apps or []):
        if manifest.router is not None:
            app.include_router(
                manifest.router,
                prefix=f"/api/apps/{manifest.name}",
                tags=[manifest.display_name],
            )
        manifests[manifest.name] = manifest

        if manifest.on_register:
            reg = AppRegistration(
                _doc_processor=doc_processor,
                _tool_registry=tool_registry,
                _export_registry=export_registry,
            )
            manifest.on_register(reg)

    # Startup recovery: mark stale running sessions as stopped
    @app.on_event("startup")
    async def _startup_recovery():
        for m in manifests.values():
            recover = getattr(
                __import__(f"apps.{m.name}.api", fromlist=["_recover_stale_sessions"]),
                "_recover_stale_sessions",
                None,
            )
            if recover:
                await recover()

    # Serve built UI if available
    ui_dist = Path(__file__).resolve().parents[2] / "ui" / "dist"
    if ui_dist.exists():
        app.mount("/", StaticFiles(directory=str(ui_dist), html=True))

    return app
