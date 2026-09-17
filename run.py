"""
Entry point for the ProtoNeo kernel (FastAPI).

Run with:
    uv run python run.py
    # or
    uv run uvicorn protoneo.api.app:create_app --factory --host 0.0.0.0 --port 5002 --reload
"""

from protoneo.cli import main

if __name__ == "__main__":
    main()
