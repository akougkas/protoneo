# CLAUDE.md

## Project

ProtoNeo v0.1.0. Multi-agent deliberation kernel with Paper Review (academic paper review).

## Architecture

- `protoneo/` is the **kernel**: agents, deliberation engine, knowledge graph, LLM client, config, tools, export, API routes
- `apps/paper_review/` is the **application layer**: conference profiles, review orchestration, pipeline, exporters
- `protoneo/api/app.py` contains `create_app()` which mounts kernel routes and app routes via `AppManifest`
- `protoneo/api/routes.py` contains kernel endpoints (health, settings, models, providers, sessions, graph, pipeline, export, parsers, tools, manifests)
- `apps/paper_review/api.py` uses `APIRouter`, mounted at `/api/apps/paper_review/` by the kernel
- `ui/` is Vue 3 + Vite. Reads branding and config from `/api/manifests` at startup.

## Import Conventions

- Kernel modules use relative imports within `protoneo.*`
- Application modules use absolute imports: `from protoneo.llm.client import LLMClient`
- The `apps/` package is a sibling to `protoneo/` at the repo root

## Commands

```bash
uv sync                                    # Install deps
uv run pytest tests/ -q                    # Run all tests (229)
uv run python run.py                       # Start kernel on :5002
protoneo                                   # CLI entry point (after pip install)
cd ui && npm install && npx vite build     # Build frontend
```

## Key Files

- `protoneo/knowledge/pipeline.py`: GraphPipeline orchestrator (6-step with checkpoint-based resume)
- `protoneo/knowledge/processor.py`: DocumentProcessor registry with parser fallback chain
- `protoneo/knowledge/parser.py`: PDF parsing entry point (pdf2md CLI with PyMuPDF fallback)
- `protoneo/export/types.py`: Exporter protocol and ExportRegistry
- `protoneo/tools/types.py`: Tool protocol and ToolRegistry
- `protoneo/config/schema.py`: ProtoNeoConfig, AppManifest, AppRegistration
- `protoneo/api/routes.py`: All kernel endpoints (health, settings, models, providers, sessions, graph, pipeline, export, parsers, tools, manifests, WebSocket)
- `protoneo/api/app.py`: App factory with manifest registration, subsystem initialization
- `protoneo/cli.py`: CLI entry point
- `apps/paper_review/api.py`: Paper Review endpoints via APIRouter
- `apps/paper_review/manifest.py`: AppManifest with router, on_register, domain_config
- `apps/paper_review/pipeline.py`: Review stage orchestration (uses kernel GraphPipeline)
- `apps/paper_review/domain/`: Entity seeds, prompts, and config YAML files

## API Route Namespacing

- Kernel routes: `/api/health`, `/api/sessions/*`, `/api/models/*`, `/api/settings/*`, `/api/export/*`, `/api/parsers`, `/api/tools`, `/api/manifests`
- App routes: `/api/apps/{app_name}/*` (e.g., `/api/apps/paper_review/conferences`)

## UI Structure

- `ui/src/views/Home.vue`: Session launcher with upload, conference select, model config
- `ui/src/views/SessionView.vue`: Session monitor with graph, agents, pipeline, results
- `ui/src/views/BatchView.vue`: Multi-session management
- `ui/src/views/SettingsView.vue`: Provider/model configuration
- `ui/src/components/ResultEditor.vue`: Post-review field refinement
- `ui/src/components/ReviewPacket.vue`: Structured review output with dynamic export formats
- `ui/src/api/kernel.js`: Centralized API client for all kernel and app endpoints

## PDF Processing Pipeline

PDF uploads are converted to markdown via `~/tools/paper-to-md` (pdf2md CLI):
1. PyMuPDF extracts raw text + images (no CPU ML models)
2. Rule-based postprocess: citations, sections, figures, bibliography, line number stripping
3. Nemotron-Cascade-2 on dynamo (LM Studio :1234): author formatting, section detection, equation reconstruction, synthesis
4. Qwen3-VL-30B on mini (llama-server :8081): figure descriptions via VLM

The `parse_file(path, fast=False)` function in `parser.py` orchestrates this. Set `fast=True` to skip AI and use PyMuPDF only. The resulting `Document.markdown` feeds the graph pipeline and reviewers.

## Testing

229 tests total: 149 kernel (`test_kernel.py`), 19 OAuth (`test_oauth.py`), 61 paper review (`test_paper_review.py`). Tests mock the LLM client and run without network.

## Do Not

- Modify files in `protoneo/` for Paper-Review-specific logic. Keep application logic in `apps/paper_review/`.
- Import from `apps.*` inside `protoneo.*`. The kernel must not depend on any application.
- Add Flask, Zep, OASIS, Neo4j, or other legacy dependencies.
