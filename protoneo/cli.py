"""ProtoNeo CLI entry point.

Boots the kernel with all registered applications and starts
the HTTP server.
"""

import argparse


def main():
    """Boot ProtoNeo kernel with registered applications."""
    parser = argparse.ArgumentParser(description="ProtoNeo deliberation kernel")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5002, help="Port (default: 5002)")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    import uvicorn

    from apps.paper_review.manifest import manifest as paper_review_manifest
    from protoneo.api.app import create_app
    from protoneo.config.schema import ProtoNeoConfig

    config = ProtoNeoConfig.from_env()
    app = create_app(config, apps=[paper_review_manifest])
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
