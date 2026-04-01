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
uv run pytest tests/ -q                    # Run all tests (247)
uv run python run.py                       # Start kernel on :5002
protoneo                                   # CLI entry point (after pip install)
cd ui && npm install && npx vite build     # Build frontend
```

## Key Files

- `protoneo/knowledge/pipeline.py`: GraphPipeline orchestrator (6-step with checkpoint-based resume)
- `protoneo/knowledge/processor.py`: DocumentProcessor registry with parser chain
- `protoneo/knowledge/parser.py`: PDF parsing entry point (Docling + integrated VLM)
- `protoneo/knowledge/parsers/docling_parser.py`: Docling-based PDF parser with figure extraction
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

PDF uploads are parsed by Docling (IBM, MIT license) with integrated VLM in a single pass:

1. Docling layout analysis classifies page regions (sections, figures, tables, captions, references)
2. Structured tables extracted via TableFormer (94-98% accuracy on scientific papers)
3. Figure bounding boxes used to crop images from pages
4. VLM describes every figure inline during parsing via Docling's `PictureDescriptionApiOptions`
5. Rich markdown output with section hierarchy, figure descriptions, and structural metadata

The `parse_file(path, vlm_config={...})` function in `parser.py` configures Docling with the VLM endpoint (llama-server with mmproj). When `fast=True`, VLM is skipped and only structural extraction runs. The resulting `Document.markdown` feeds the graph pipeline and reviewers.

## Testing

247 tests total: kernel (`test_kernel.py`), OAuth (`test_oauth.py`), paper review (`test_paper_review.py`). Tests mock the LLM client and run without network.

## Do Not

- Modify files in `protoneo/` for Paper-Review-specific logic. Keep application logic in `apps/paper_review/`.
- Import from `apps.*` inside `protoneo.*`. The kernel must not depend on any application.
- Add Flask, Zep, OASIS, Neo4j, PyMuPDF, or other legacy dependencies.
