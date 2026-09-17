"""ProtoNeo CLI entry point.

Boots the kernel with all registered applications and starts
the HTTP server.
"""

import argparse
import importlib
from importlib.metadata import entry_points
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


def create_cli_app():
    """Importable factory so reload workers use the same application selection."""
    from protoneo.api.app import create_app
    from protoneo.config.schema import ProtoNeoConfig

    specs = [spec.strip() for spec in os.getenv("PROTONEO_APPS", "").split(",") if spec.strip()]
    if specs:
        manifests = [_load_app_manifest(spec) for spec in dict.fromkeys(specs)]
    else:
        manifests = [entry.load() for entry in sorted(entry_points(group="protoneo.apps"), key=lambda e: e.name)]
    return create_app(ProtoNeoConfig.from_env(), apps=manifests)


def main():
    """Boot ProtoNeo kernel with registered applications."""
    parser = argparse.ArgumentParser(description="ProtoNeo deliberation kernel")
    parser.add_argument("--host", default=os.getenv("PROTONEO_HOST", "0.0.0.0"), help="Bind address (or PROTONEO_HOST)")
    parser.add_argument("--port", type=int, default=os.getenv("PROTONEO_PORT", "5002"), help="Port (or PROTONEO_PORT; default: 5002)")
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

    env_apps = [
        spec.strip()
        for spec in os.getenv("PROTONEO_APPS", "").split(",")
        if spec.strip()
    ]
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.app:
        os.environ["PROTONEO_APPS"] = ",".join(dict.fromkeys([*env_apps, *args.app]))
    uvicorn.run("protoneo.cli:create_cli_app", factory=True, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
