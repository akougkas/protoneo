"""
ProtoNeo application factory.

Creates the FastAPI app, initializes kernel subsystem registries,
registers kernel routes, and mounts application routes.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config.schema import AppManifest, AppRegistration, ProtoNeoConfig
from ..export import create_export_registry
from ..knowledge import create_document_processor
from ..tools import create_tool_registry
from .events import SessionEventBus
from .pipeline_control import PipelineControl
from .routes import register_kernel_routes


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

    # Store on app.state for kernel routes
    app.state.document_processor = doc_processor
    app.state.tool_registry = tool_registry
    app.state.export_registry = export_registry
    app.state.manifests = manifests

    # Register kernel routes (health, settings, models, sessions, etc.)
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

    # Legacy: mount Paper Review application directly
    # (will be converted to AppManifest in a later session)
    from apps.paper_review.api import register_paper_review_routes
    register_paper_review_routes(app)

    return app
