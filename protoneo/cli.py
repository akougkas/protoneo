"""ProtoNeo CLI entry point.

Boots the kernel with all registered applications and starts
the HTTP server.
"""

import argparse
import importlib
import os

from protoneo.config.schema import AppManifest


def _load_app_manifest(spec: str) -> AppManifest:
    """Load an AppManifest from module:attribute without kernel app imports."""
    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name or not attr:
        raise SystemExit(f"Invalid --app spec {spec!r}; expected module:attribute")
    manifest = getattr(importlib.import_module(module_name), attr)
    if not isinstance(manifest, AppManifest):
        raise SystemExit(f"{spec!r} did not resolve to an AppManifest")
    return manifest


def main():
    """Boot ProtoNeo kernel with registered applications."""
    parser = argparse.ArgumentParser(description="ProtoNeo deliberation kernel")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5002, help="Port (default: 5002)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    parser.add_argument(
        "--app",
        action="append",
        default=[],
        metavar="MODULE:ATTR",
        help="App manifest to mount; may also be set via PROTONEO_APPS",
    )
    args = parser.parse_args()

    import uvicorn

    from protoneo.api.app import create_app
    from protoneo.config.schema import ProtoNeoConfig

    env_apps = [
        spec.strip()
        for spec in os.getenv("PROTONEO_APPS", "").split(",")
        if spec.strip()
    ]
    apps = [_load_app_manifest(spec) for spec in [*env_apps, *args.app]]

    config = ProtoNeoConfig.from_env()
    app = create_app(config, apps=apps)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
