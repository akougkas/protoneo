# ProtoNeo Kernel

ProtoNeo is a composable runtime for building applications that require LLM councils. The kernel provides agent primitives, deliberation patterns, knowledge graph generation, multi-provider LLM routing, session management, a real-time event system, pluggable document processing, structured export, and a browser-based dashboard.

Applications are thin domain specializations that configure the kernel's capabilities for a specific use case.

## Architecture

```
protoneo/
  agents/          Agent interface and base implementation
  deliberation/    Engine, patterns (parallel, sequential, round-robin, independent synthesis)
  knowledge/       Document processing, ontology generation, graph extraction, verification
  llm/             Multi-provider LLM client, model registry, OAuth providers
  tools/           External tool registry (Semantic Scholar, web search)
  export/          Output format registry (JSON, Markdown, app-contributed formats)
  config/          Configuration schema, AppManifest, AppRegistration
  api/             FastAPI routes, WebSocket event bus, pipeline control
```

## Extension Points

The kernel exposes eight extension points. Applications interact with these through structured interfaces, never through raw framework access.

### Document Processing (Parser Protocol + DocumentProcessor)

Register custom document parsers that convert files into structured text. The `DocumentProcessor` tries parsers in priority order with automatic fallback.

```python
from protoneo.knowledge.types import Parser, ParseResult

class MyParser:
    @property
    def name(self) -> str: return "my_parser"

    @property
    def supported_extensions(self) -> set[str]: return {".pdf"}

    def available(self) -> bool: return True

    async def parse(self, path, options=None) -> ParseResult:
        text = extract_text(path)
        return ParseResult(text=text, markdown=text)
```

Built-in parsers: `PlainTextParser` (.txt), `MarkdownParser` (.md), `PyMuPDFParser` (.pdf, priority 10), `Pdf2MdParser` (.pdf, AI-powered, priority 20).

### Knowledge Pipeline (Parameterized by DomainConfig)

The 6-step pipeline is kernel-owned. Applications inject domain expertise through `DomainConfig`:

1. **Metadata** extraction (sections, title, abstract, citations, figures)
2. **Ontology** generation (entity/edge types from domain seeds + LLM discovery)
3. **Graph extraction** (section-aware entity and relationship extraction)
4. **Coreference resolution** (merge duplicate entities, create aliases)
5. **Verification** (3-pass audit: grounding, completeness, connectivity)
6. **Summary** generation (agent briefing, structural links, pruning)

Each step writes a durable checkpoint to the session. On resume, completed stages are skipped.

### Deliberation Patterns

Four built-in patterns orchestrate agent interactions:

| Pattern | Behavior |
|---------|----------|
| `SequentialPattern` | Agents execute in order, each seeing prior outputs |
| `ParallelPattern` | All agents execute concurrently |
| `RoundRobinPattern` | Multiple rounds with variance-triggered continuation |
| `IndependentSynthesisPattern` | Multi-phase: parallel review, deliberation, synthesis |

### LLM Providers

Multi-provider routing through LiteLLM (local endpoints, API keys) and direct HTTP (OAuth). Providers are discovered and configured through the Settings UI.

### Tools (Tool Protocol + ToolRegistry)

Register external capabilities available to agents:

```python
from protoneo.tools.types import Tool, ToolResult

class MyTool:
    @property
    def name(self) -> str: return "my_tool"

    @property
    def description(self) -> str: return "Does something useful"

    def available(self) -> bool: return True

    async def execute(self, query, **kwargs) -> ToolResult:
        return ToolResult(data={"result": "..."}, source="my_tool")
```

### Export Formats (Exporter Protocol + ExportRegistry)

Register output format renderers:

```python
from protoneo.export.types import Exporter

class MyExporter:
    @property
    def format_name(self) -> str: return "custom"

    @property
    def mime_type(self) -> str: return "text/plain"

    @property
    def file_extension(self) -> str: return ".txt"

    async def export(self, session, app_data=None) -> bytes:
        return b"exported content"
```

Built-in: `JsonExporter` (raw session JSON), `GenericMarkdownExporter` (deliberation results as Markdown).

### Event Delivery (SessionEventBus)

In-memory queue-based event broadcaster for real-time UI updates over WebSocket. Events include `step_started`, `graph_updated`, `ontology_ready`, `agent_streaming`, `stage_complete`, and more.

### App Registration (AppManifest)

Applications register through `AppManifest`, which specifies:

- API router (mounted under `/api/apps/{name}/`)
- Registration callback (contributes parsers, exporters, tools)
- Domain configuration (seeds, prompts, patterns)
- Pipeline stages (appended after kernel stages)
- Output rendering hints (score fields, result schema)

## API Reference

### Health and Manifests

```
GET  /api/health
GET  /api/manifests                    # List registered apps
GET  /api/manifests/{app_name}         # Get specific app manifest
```

### Settings and Models

```
GET  /api/settings
PUT  /api/settings
GET  /api/settings/active-models
GET  /api/presets
POST /api/presets/{name}/activate
GET  /api/models
POST /api/models/discover
POST /api/models/benchmark
GET  /api/models/benchmark
```

### Providers

```
GET  /api/providers
GET  /api/providers/{name}
POST /api/providers/login
POST /api/providers/callback
GET  /api/providers/{name}/login-status
POST /api/providers/{name}/logout
```

### Sessions

```
POST /api/sessions
GET  /api/sessions
GET  /api/sessions/{id}
POST /api/sessions/{id}/upload
POST /api/sessions/{id}/start
POST /api/sessions/{id}/stop
POST /api/sessions/{id}/retry
WS   /api/sessions/{id}/stream
```

### Graph

```
GET  /api/sessions/{id}/graph
GET  /api/sessions/{id}/graph/step/{name}
GET  /api/sessions/{id}/graph/export
GET  /api/sessions/{id}/ontology
POST /api/sessions/{id}/generate-ontology
POST /api/sessions/{id}/extract-graph
GET  /api/sessions/{id}/graph-utilization
GET  /api/sessions/{id}/graph-summary
```

### Pipeline

```
GET  /api/sessions/{id}/pipeline
POST /api/sessions/{id}/pipeline/advance
POST /api/sessions/{id}/pipeline/pause
POST /api/sessions/{id}/pipeline/resume
POST /api/sessions/{id}/pipeline/cancel
POST /api/sessions/{id}/pipeline/step/{name}/run
POST /api/sessions/{id}/pipeline/step/{name}/cancel
POST /api/sessions/{id}/pipeline/edit-ontology
```

### Export, Parsers, Tools

```
GET  /api/export/formats
GET  /api/sessions/{id}/export?format=X
GET  /api/parsers
GET  /api/tools
```

## Session Model

Sessions carry ownership and pipeline state:

| Field | Purpose |
|-------|---------|
| `session_id` | Unique identifier |
| `schema_version` | Schema version for forward migration |
| `app_name` | Owning application (set at creation) |
| `app_version` | App version at creation time |
| `document_text` | Parsed document text |
| `document_markdown` | Markdown-formatted document |
| `knowledge_graph` | Generated knowledge graph (JSON) |
| `checkpoints` | Completed pipeline stage records |
| `pipeline_steps` | Step-level status tracking |
| `app_data` | Application-specific storage namespace |
| `result` | Deliberation output |

## Pipeline Checkpoints

Every pipeline stage writes a `StageCheckpoint` recording the stage name, completion time, and output location. On resume:

1. The pipeline checks for existing checkpoints
2. Completed stages are skipped
3. Execution resumes from the last incomplete stage
4. Graph state is restored from the last checkpoint's snapshot

This enables reliable recovery after failures or interruptions without re-running completed work.
