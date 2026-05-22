"""
Entry point for the ProtoNeo kernel (FastAPI).

Run with:
    uv run python run.py
    # or
    uv run uvicorn protoneo.api.app:create_app --factory --host 0.0.0.0 --port 5002 --reload
"""

import uvicorn

from apps.paper_review.manifest import manifest as paper_review_manifest
from protoneo.api.app import create_app
from protoneo.config.schema import ProtoNeoConfig

if __name__ == "__main__":
    config = ProtoNeoConfig.from_env()
    app = create_app(config, apps=[paper_review_manifest])
    uvicorn.run(app, host="0.0.0.0", port=5002)
