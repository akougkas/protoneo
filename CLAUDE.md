# CLAUDE.md

## Project

ProtoNeo v0.1.0. Multi-agent deliberation kernel with Paper Review (academic paper review).

## Architecture

- `protoneo/` is the **kernel**: agents, deliberation engine, knowledge graph, LLM client, config, tools, API routes
- `apps/paper_review/` is the **application layer**: conference profiles, review orchestration, pipeline, export
- `protoneo/api/app.py` contains `create_app()` which mounts both kernel routes and Paper Review routes
- `protoneo/api/routes.py` contains kernel endpoints plus `SessionEventBus` and `PipelineControl` shared classes
- `ui/` is Vue 3 + Vite, Paper Review only (no legacy simulation views)

## Import Conventions

- Kernel modules use relative imports within `protoneo.*`
- Application modules use absolute imports: `from protoneo.llm.client import LLMClient`
- The `apps/` package is a sibling to `protoneo/` at the repo root

## Commands

```bash
uv sync                                    # Install deps
uv run pytest tests/test_kernel.py -q      # Run tests
uv run python run.py                       # Start kernel on :5002
cd ui && npm install && npx vite build     # Build frontend
```

## Key Files

- `protoneo/knowledge/parser.py`: PDF parsing entry point. Calls `pdf2md` CLI (subprocess) for AI-powered markdown extraction, falls back to PyMuPDF plain text
- `protoneo/api/routes.py`: All kernel endpoints (health, settings, models, providers, sessions, WebSocket)
- `apps/paper_review/api.py`: All Paper Review endpoints (review, batch, graph, pipeline control)
- `apps/paper_review/pipeline.py`: 7-step graph pipeline and review stage orchestration
- `apps/paper_review/conference.py`: Conference profile loading (profiles/ directory)
- `apps/paper_review/prompts.py`: Prompt template loading (prompts/ directory)

## PDF Processing Pipeline

PDF uploads are converted to markdown via `~/tools/paper-to-md` (pdf2md CLI):
1. PyMuPDF extracts raw text + images (no CPU ML models)
2. Rule-based postprocess: citations, sections, figures, bibliography, line number stripping
3. Nemotron-Cascade-2 on dynamo (LM Studio :1234): author formatting, section detection, equation reconstruction, synthesis
4. Qwen3-VL-30B on mini (llama-server :8081): figure descriptions via VLM

The `parse_file(path, fast=False)` function in `parser.py` orchestrates this. Set `fast=True` to skip AI and use PyMuPDF only. The resulting `Document.markdown` feeds the graph pipeline and reviewers.

## Testing

108 kernel tests in `tests/test_kernel.py`. OAuth tests in `tests/test_oauth.py`. Tests mock the LLM client and run without network.

## Do Not

- Modify files in `protoneo/` for Paper-Review-specific logic. Keep application logic in `apps/paper_review/`.
- Import from `apps.*` inside `protoneo.*`. The kernel must not depend on any application.
- Add Flask, Zep, OASIS, Neo4j, or other legacy dependencies.
