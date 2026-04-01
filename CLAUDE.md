# CLAUDE.md

ProtoNeo v0.1.0. Multi-agent deliberation kernel. First app: Paper Review (academic paper review).

## Architecture

- `protoneo/` is the kernel. `apps/paper_review/` is the application layer. `ui/` is Vue 3 + Vite.
- Kernel modules use relative imports. App modules use absolute imports from `protoneo.*`.
- Settings persist to `~/.protoneo/settings.json` via `ProtoNeoSettings` in `protoneo/llm/settings.py`.
- VLM config flows through `build_vlm_config()` to all `parse_file()` callers.

## Commands

```bash
uv sync                                    # Install deps
uv run pytest tests/ -q                    # Run all tests (245)
uv run python run.py                       # Start kernel on :5002
cd ui && npm install && npx vite build     # Build frontend
```

## Rules

- Never import from `apps.*` inside `protoneo/`. The kernel must not depend on any application.
- Never add Flask, Zep, OASIS, Neo4j, PyMuPDF, or other legacy dependencies.
- Keep Paper-Review-specific logic in `apps/paper_review/`, not in `protoneo/`.
- Conference selection is required (no defaults). The UI gates launch on venue selection.

## Gotchas

- `parse_file()` in `run_in_executor` needs a lambda for keyword args (positional spread cannot pass `vlm_config`).
- FastAPI `Form(...)` (with Ellipsis) makes a form field required. `Form("default")` sets a default.
- Docling's `PictureDescriptionApiOptions` handles VLM inline. No separate enrichment step exists.
- Tests mock the LLM client and run without network. 245 total across kernel, OAuth, and paper review.
