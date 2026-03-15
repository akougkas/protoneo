"""
ProtoNeo application factory.

Creates the FastAPI app, registers kernel routes, and mounts
the PC Panel application routes.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config.schema import ProtoNeoConfig
from .routes import PipelineControl, SessionEventBus, register_kernel_routes


def create_app(config: ProtoNeoConfig | None = None) -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(title="ProtoNeo", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register kernel routes (health, settings, models, sessions, etc.)
    register_kernel_routes(app, config)

    # Mount PC Panel application
    from applications.pc_panel.api import register_pc_panel_routes
    register_pc_panel_routes(app)

    return app
