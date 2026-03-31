# ProtoNeo v0.1.0 Architecture Plan

Supersedes PLAN.md, PIPELINE-FIXES.md, SESSION_PROMPTS.md. All prior work is
complete. This document describes the clean-slate restructuring for open source
release.

## What ProtoNeo Is

ProtoNeo is a composable runtime for building applications that require LLM
councils. It provides agent primitives, deliberation patterns, knowledge graph
generation, multi-provider LLM routing, session management, a real-time event
system, pluggable document processing, structured export, and a browser-based
dashboard. Applications are thin domain specializations that configure the
kernel's capabilities for a specific use case.

The paradigm is Linux kernel + distros. ProtoNeo is the kernel. Applications
are opinionated distros built on top. The kernel provides all intelligence,
wiring, and infrastructure. Applications bring domain-specific prompts,
ontology seeds, output schemas, and pipeline configurations.

## First Application: Paper Review

An AI peer review panel for academic papers. Authors upload a PDF, the kernel
builds a knowledge graph, runs a panel of reviewer agents through structured
deliberation, and produces a review packet with scores, strengths, weaknesses,
and revision guidance. Targets systems conferences (HPDC, SC, SOSP) for
pre-submission self-assessment.

Future applications (not in scope for v0.1.0): patent review, policy review,
code review, grant proposal review. Each would bring its own domain config
and reuse the same kernel.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                      Application Layer                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ paper_review  │  │patent_review │  │ code_review  │ ...   │
│  │              │  │  (future)    │  │  (future)    │       │
│  │ profiles/    │  │              │  │              │       │
│  │ prompts/     │  │              │  │              │       │
│  │ domain/      │  │              │  │              │       │
│  │ schemas.py   │  │              │  │              │       │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘       │
│         │                 │                 │                │
│         ▼                 ▼                 ▼                │
│  ┌────────────────────────────────────────────────────────┐  │
│  │               AppManifest + DomainConfig                │  │
│  │   (registration contract, domain seeds, prompts,        │  │
│  │    custom exporters, custom parsers)                     │  │
│  └────────────────────────┬───────────────────────────────┘  │
└───────────────────────────┼──────────────────────────────────┘
                            │
┌───────────────────────────┼──────────────────────────────────┐
│                      ProtoNeo Kernel                          │
│                           │                                  │
│  ┌───────────┐  ┌────────┴────────┐  ┌────────────────────┐ │
│  │ agents/   │  │  deliberation/  │  │    knowledge/      │ │
│  │           │  │                 │  │                    │ │
│  │ protocol  │  │ engine          │  │ processor (reg.)   │ │
│  │ base      │  │ patterns        │  │ parsers/ (strat.)  │ │
│  │ types     │  │ session         │  │ ontology (param)   │ │
│  └───────────┘  └─────────────────┘  │ graph (param)      │ │
│                                      │ extractor (param)   │ │
│  ┌───────────┐  ┌─────────────────┐  │ verifier (param)   │ │
│  │  llm/     │  │     api/        │  │ pipeline           │ │
│  │           │  │                 │  │ coref              │ │
│  │ client    │  │ routes          │  │ chunker            │ │
│  │ registry  │  │ events          │  │ metadata           │ │
│  │ settings  │  │ pipeline_ctl    │  └────────────────────┘ │
│  │ providers │  │ app factory     │                         │
│  │ discovery │  └─────────────────┘  ┌────────────────────┐ │
│  └───────────┘                       │   tools/ (reg.)    │ │
│                 ┌─────────────────┐   │ semantic_scholar   │ │
│  ┌───────────┐  │    export/      │   │ web_search         │ │
│  │  config/  │  │  (registry)     │   └────────────────────┘ │
│  │           │  │ json_exporter   │                         │
│  │ schema    │  │ md_exporter     │   ┌────────────────────┐ │
│  │ manifest  │  │ pdf_exporter    │   │       ui/          │ │
│  └───────────┘  └─────────────────┘   │  dashboard (Vue)   │ │
│                                       └────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

Legend:
- `(reg.)` = Registry pattern (pluggable components discovered by name)
- `(strat.)` = Strategy pattern (swappable implementations behind a protocol)
- `(param)` = Parameterized (algorithm is kernel, prompts/seeds injected by app)

---

## Design Patterns

Eight extension points. Three are already done. Five are formalized here.

| Extension Point | Pattern | Interface | v0.1.0 |
|----------------|---------|-----------|--------|
| Document processing | Strategy + Registry + Chain of Responsibility | `Parser` protocol, `DocumentProcessor` | Yes |
| Knowledge pipeline | Parameterized functions (DomainConfig injection) | `generate_ontology(domain_config, ...)` | Yes |
| Deliberation modes | Strategy (registered by name) | `Pattern` protocol | Done |
| LLM providers | Strategy + Registry | `OAuthProvider`, `LLMClient` | Done |
| Tools | Strategy + Registry | `Tool` protocol, `ToolRegistry` | Yes |
| Export formats | Strategy + Registry | `Exporter` protocol, `ExportRegistry` | Yes |
| Event delivery | Observer | `SessionEventBus` | Done |
| App registration | Plugin manifest | `AppManifest` | Yes |

### Parser Protocol + DocumentProcessor

The current `parse_file()` is a hardcoded if/elif chain that routes by file
extension. Adding a new parsing backend (LiteParse, Docling, Nougat) requires
editing kernel code. That violates open/closed principle.

**Parser** (Strategy): knows how to convert one file type into structured text.

```python
# protoneo/knowledge/types.py

@dataclass
class ParseResult:
    """Output of a single parser."""
    text: str
    markdown: str = ""
    figures_dir: str = ""
    metadata: dict = field(default_factory=dict)

class Parser(Protocol):
    """A document parsing backend. Stateless, registered once at startup."""

    @property
    def name(self) -> str: ...

    @property
    def supported_extensions(self) -> set[str]: ...

    def available(self) -> bool:
        """Check if dependencies are installed (import check, binary check)."""
        ...

    async def parse(self, path: Path, options: dict | None = None) -> ParseResult: ...
```

**DocumentProcessor** (Registry + Chain of Responsibility): knows which parsers
exist, which are available, and tries them in priority order with fallback.

```python
# protoneo/knowledge/processor.py

class DocumentProcessor:
    """Registry of parsers with priority-based fallback chains."""

    def register_parser(self, parser: Parser, priority: int = 0) -> None:
        """Register a parser. Higher priority = tried first."""

    def register_post_processor(self, fn: Callable[[str], str]) -> None:
        """Register a text cleanup function applied after parsing."""

    def available_parsers(self, extension: str) -> list[str]:
        """List available parser names for a file extension."""

    async def process(
        self,
        path: Path,
        preferred_parser: str | None = None,
    ) -> Document:
        """Process a file into a Document.

        Tries parsers in priority order. If preferred_parser is set,
        tries that one first. Falls back through the chain on failure.
        """
```

**Built-in parsers** (ship with kernel):

| Parser | File | Extensions | Priority | Dependencies |
|--------|------|-----------|----------|--------------|
| `PlainTextParser` | `parsers/plaintext.py` | `.txt`, `.text` | 0 | None (always available) |
| `MarkdownParser` | `parsers/markdown.py` | `.md`, `.markdown` | 0 | None (always available) |
| `PyMuPDFParser` | `parsers/pymupdf.py` | `.pdf` | 10 | `PyMuPDF` (in deps) |
| `Pdf2MdParser` | `parsers/pdf2md.py` | `.pdf` | 20 | `pdf2md` CLI binary |

**Registration at kernel startup:**

```python
# protoneo/knowledge/__init__.py

def create_document_processor() -> DocumentProcessor:
    proc = DocumentProcessor()
    proc.register_parser(PlainTextParser(), priority=0)
    proc.register_parser(MarkdownParser(), priority=0)
    proc.register_parser(PyMuPDFParser(), priority=10)
    proc.register_parser(Pdf2MdParser(), priority=20)
    proc.register_post_processor(strip_line_number_pollution)
    return proc
```

Applications can add parsers through the `on_register` callback:

```python
# In an app's on_register callback:
def _on_register(reg: AppRegistration):
    reg.register_parser(GrobidParser(), priority=15)
```

Adding a new parser (e.g., LiteParse) requires one file and one registration
call. No existing kernel code is modified.

### Tool Protocol + ToolRegistry

Currently `semantic_scholar.py` and `web_search.py` are loose modules with no
shared interface. Agents cannot discover or invoke them uniformly.

```python
# protoneo/tools/types.py

@dataclass
class ToolResult:
    data: dict
    source: str
    cached: bool = False

class Tool(Protocol):
    """An external capability available to agents."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str:
        """One-line description for agent tool-use prompts."""
        ...

    def available(self) -> bool:
        """Check if API key or service endpoint is configured."""
        ...

    async def execute(self, query: str, **kwargs) -> ToolResult: ...


class ToolRegistry:
    """Registry of kernel tools. Agents receive the available_tools()
    list in their context for tool-use capabilities."""

    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool | None: ...
    def available_tools(self) -> list[dict]:
        """Return [{name, description}] for all available tools."""
        ...
```

Built-in tools: `SemanticScholarTool`, `WebSearchTool`. Applications can
register additional domain-specific tools via the manifest.

### Exporter Protocol + ExportRegistry

Export is a kernel capability. The kernel provides the protocol, the registry,
the API endpoint (`GET /api/sessions/{id}/export?format=X`), and built-in
exporters. Applications register domain-specific exporters that render their
result schemas into presentation formats.

```python
# protoneo/export/types.py

class Exporter(Protocol):
    """Renders session results into a specific output format."""

    @property
    def format_name(self) -> str:
        """Lookup key: 'json', 'markdown', 'pdf', 'latex'."""
        ...

    @property
    def mime_type(self) -> str: ...

    @property
    def file_extension(self) -> str: ...

    async def export(self, session: Session, app_data: dict | None = None) -> bytes:
        """Render session results. app_data comes from session.app_data."""
        ...


class ExportRegistry:
    """Registry of export formats."""

    def register(self, exporter: Exporter) -> None: ...
    def get(self, format_name: str) -> Exporter | None: ...
    def available_formats(self) -> list[dict]:
        """Return [{format_name, mime_type, file_extension}]."""
        ...
```

**Built-in exporters** (ship with kernel):

| Exporter | Format | Purpose |
|----------|--------|---------|
| `JsonExporter` | `json` | Raw session result as JSON |
| `GenericMarkdownExporter` | `markdown` | Deliberation results as readable Markdown |

**Application-contributed exporters** (registered via manifest):

| Exporter | Format | Purpose |
|----------|--------|---------|
| `ReviewMarkdownExporter` | `review-markdown` | Paper review packet as formatted Markdown |
| `ReviewPdfExporter` | `review-pdf` | Paper review packet as PDF via WeasyPrint |

The kernel API endpoint:

```
GET  /api/sessions/{id}/export?format=json
GET  /api/sessions/{id}/export?format=review-markdown
GET  /api/sessions/{id}/export?format=review-pdf
GET  /api/export/formats                                # List available formats
```

Applications register their exporters through the `on_register` callback:

```python
# apps/paper_review/manifest.py
def _on_register(reg: AppRegistration):
    reg.register_exporter(ReviewMarkdownExporter())
    reg.register_exporter(ReviewPdfExporter())
```

The kernel registers its built-in exporters at startup and merges application
exporters during manifest registration. The export endpoint resolves the
owning app from `session.app_name` to determine which exporters are available.
The UI reads available formats from `/api/export/formats` and offers them in
the export dropdown.

### Knowledge Pipeline Stages: Parameterized, Not Polymorphic

The pipeline stages have fundamentally different signatures:

- `extract_metadata(text) → DocumentMetadata`
- `generate_ontology(text, domain_config, llm) → Ontology`
- `extract_graph(text, ontology, domain_config, llm) → KnowledgeGraph`
- `resolve_coreferences(graph, llm) → KnowledgeGraph`
- `verify_graph(graph, text, domain_config, llm) → VerificationResult`

Forcing these into a shared `PipelineStage` protocol would require `Any` for
inputs and outputs, destroying type safety for no benefit. Instead, each stage
is a well-typed function that accepts `DomainConfig` where domain expertise
is needed. The `GraphPipeline` orchestrator calls them in sequence.

Each stage function accepts an optional `strategy` parameter for future
extensibility:

```python
async def extract_graph(
    text: str,
    ontology: Ontology,
    domain_config: DomainConfig,
    llm: LLMClient,
    *,
    strategy: str = "llm_batch",   # future: "ner", "rule_based"
    on_progress: EventCallback = None,
) -> KnowledgeGraph:
```

For v0.1.0, only one strategy per stage is implemented. The parameter exists
so the interface is stable when more strategies arrive.

---

## Directory Structure

```
protoneo/
│
├── protoneo/                          # KERNEL PACKAGE
│   ├── __init__.py                    # __version__, public imports
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── types.py                   # Message, AgentOutput, Document, GroundingSource
│   │   ├── protocol.py                # AgentProtocol, SessionContext protocols
│   │   └── base.py                    # BaseAgent (LLM-backed default)
│   │
│   ├── deliberation/
│   │   ├── __init__.py
│   │   ├── types.py                   # DeliberationRules, PhaseResult, DeliberationResult
│   │   ├── engine.py                  # DeliberationEngine orchestrator
│   │   ├── patterns.py                # Sequential, Parallel, RoundRobin, IndependentSynthesis
│   │   └── session.py                 # Session, SessionManager, BatchManager, StepState
│   │
│   ├── knowledge/
│   │   ├── __init__.py                # create_document_processor() factory
│   │   ├── types.py                   # DomainConfig, SeedEntity, SeedEdge, ParseResult, Parser protocol
│   │   ├── processor.py               # DocumentProcessor (registry + chain of responsibility)
│   │   ├── parsers/                   # Built-in parser implementations
│   │   │   ├── __init__.py
│   │   │   ├── plaintext.py           # PlainTextParser (.txt, .text)
│   │   │   ├── markdown.py            # MarkdownParser (.md, .markdown)
│   │   │   ├── pymupdf.py             # PyMuPDFParser (.pdf, fallback)
│   │   │   └── pdf2md.py              # Pdf2MdParser (.pdf, AI-powered, highest priority)
│   │   ├── chunker.py                 # Sentence-boundary chunking
│   │   ├── metadata.py                # Section/title/abstract extraction
│   │   ├── ontology.py                # Ontology generation (parameterized by DomainConfig)
│   │   ├── graph.py                   # KnowledgeGraph model, D3 export, agent briefing
│   │   ├── extractor.py              # Section-aware graph extraction (parameterized)
│   │   ├── coref.py                   # Coreference resolution
│   │   ├── verifier.py                # 3-pass graph verification (parameterized)
│   │   └── pipeline.py                # GraphPipeline orchestrator
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── types.py                   # ModelInfo, LLMResponse, TokenUsage, ModelCapability
│   │   ├── client.py                  # LLMClient (multi-provider, OAuth routing)
│   │   ├── registry.py                # CapabilityRegistry
│   │   ├── settings.py                # ProtoNeoSettings, endpoint config, presets
│   │   ├── catalogs.py                # Static model catalogs
│   │   ├── discovery.py               # LM Studio/Ollama/cloud endpoint probing
│   │   ├── benchmark.py               # Model benchmarking
│   │   ├── models_dev.py              # models.dev API integration
│   │   └── providers/
│   │       ├── __init__.py
│   │       ├── oauth_base.py          # PKCE OAuth base + token storage
│   │       ├── openai_oauth.py        # OpenAI/ChatGPT OAuth
│   │       ├── anthropic_oauth.py     # Anthropic OAuth (disabled, reference)
│   │       └── registry.py            # Provider registry
│   │
│   ├── export/                        # KERNEL EXPORT SUBSYSTEM
│   │   ├── __init__.py                # create_export_registry() factory
│   │   ├── types.py                   # Exporter protocol, ExportRegistry
│   │   ├── json_exporter.py           # Built-in: raw session JSON
│   │   └── markdown_exporter.py       # Built-in: generic deliberation Markdown
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   └── schema.py                  # ProtoNeoConfig, AgentConfig, AppManifest
│   │
│   ├── tools/
│   │   ├── __init__.py                # create_tool_registry() factory
│   │   ├── types.py                   # Tool protocol, ToolResult, ToolRegistry
│   │   ├── semantic_scholar.py        # SemanticScholarTool
│   │   └── web_search.py             # WebSearchTool (Brave/SearXNG/DDG)
│   │
│   └── api/
│       ├── __init__.py
│       ├── app.py                     # create_app() with manifest registration
│       ├── routes.py                  # ALL kernel endpoints
│       ├── events.py                  # SessionEventBus
│       └── pipeline_control.py        # PipelineControl
│
├── ui/                                # KERNEL UI (deliberation dashboard)
│   ├── src/
│   │   ├── api/
│   │   │   └── kernel.js             # Axios client for kernel + app endpoints
│   │   ├── views/
│   │   │   ├── Home.vue              # Session launcher: profile select, upload, model config
│   │   │   ├── SessionView.vue       # Session monitor: graph, agents, pipeline, results
│   │   │   ├── BatchView.vue         # Multi-session management
│   │   │   └── SettingsView.vue      # Provider/model configuration
│   │   ├── components/
│   │   │   ├── AgentCard.vue         # Agent status + streaming output
│   │   │   ├── GraphPanel.vue        # D3 knowledge graph visualization
│   │   │   ├── SessionPanel.vue      # Pipeline stages, gates, step progress
│   │   │   ├── ResultPacket.vue      # Structured deliberation output (schema-driven)
│   │   │   └── ResultEditor.vue      # Manual output refinement
│   │   ├── store/
│   │   │   └── session.js            # Reactive session state
│   │   ├── router/
│   │   │   └── index.js              # Routes: /, /session/:id, /batch/:id, /settings
│   │   ├── utils/
│   │   │   └── markdown.js           # Lightweight markdown renderer
│   │   ├── App.vue                   # Root: reads app manifest for branding
│   │   └── main.js
│   ├── index.html
│   ├── package.json                   # vue, vue-router, axios, d3
│   └── vite.config.js
│
├── apps/                              # APPLICATION LAYER
│   └── paper_review/
│       ├── __init__.py                # Exports manifest
│       ├── manifest.py                # AppManifest + DomainConfig loading
│       ├── api.py                     # Domain-specific routes (~12 endpoints)
│       ├── pipeline.py                # Review stage orchestration (deliberation wiring)
│       ├── review.py                  # Agent config builder, output parsers
│       ├── schemas.py                 # ReviewPacket, IndividualReview, MetaReview
│       ├── conference.py              # Venue profile loading
│       ├── preflight.py               # Pre-review manuscript heuristics
│       ├── prompts.py                 # Reviewer prompt template assembly
│       ├── exporters.py               # ReviewMarkdownExporter, ReviewPdfExporter
│       ├── domain/                    # Domain knowledge injected into kernel
│       │   ├── config.yaml            # structural_types, summary settings
│       │   ├── seeds.yaml             # Base + domain entity/edge types
│       │   ├── domain_patterns.yaml   # Keyword-to-domain mapping
│       │   └── prompts/
│       │       ├── ontology_discovery.md
│       │       ├── ontology_grounding.md
│       │       ├── extraction.md
│       │       ├── verify_connectivity.md
│       │       ├── verify_completeness.md
│       │       ├── verify_grounding.md
│       │       └── graph_summary.md
│       ├── profiles/                  # Venue configurations
│       │   ├── hpdc26.profile.yaml
│       │   └── sc26.profile.yaml
│       └── prompts/                   # Reviewer prompt templates
│           ├── hpdc26/
│           │   ├── prompt-pack.yaml
│           │   ├── shared.md
│           │   ├── technical.md
│           │   ├── novelty.md
│           │   ├── clarity.md
│           │   ├── skeptic.md
│           │   ├── meta.md
│           │   └── artifact.md
│           └── sc26/
│               ├── prompt-pack.yaml
│               ├── shared.md
│               ├── technical.md
│               ├── systems.md
│               ├── novelty.md
│               ├── skeptic.md
│               └── meta.md
│
├── tests/
│   ├── test_kernel.py                 # Kernel unit tests (~110 tests)
│   ├── test_paper_review.py           # Application tests (~25 tests)
│   ├── test_oauth.py                  # OAuth flow tests
│   └── conftest.py                    # Shared fixtures
│
├── docs/
│   ├── kernel.md                      # Kernel capabilities and extension points
│   ├── building-apps.md               # How to build an application on ProtoNeo
│   └── paper-review.md                # Paper review app usage and configuration
│
├── pyproject.toml                     # Single package: protoneo + apps
├── run.py                             # Entry point: boots kernel + registered apps
├── CLAUDE.md                          # Developer reference
├── README.md                          # Public documentation
└── .gitignore
```

---

## Kernel Design

### agents/

No changes from current implementation. Already clean and generic.

- `AgentProtocol`: stateless interface any agent must satisfy
- `BaseAgent`: default LLM-backed agent with role, model, system prompt
- `Document`: carrier for text, markdown, chunks, metadata
- `Message`, `AgentOutput`: deliberation communication types

### deliberation/

No changes from current implementation. Already generic.

- `DeliberationEngine`: orchestrates agents through configured patterns
- `patterns.py`: Sequential, Parallel, RoundRobin, IndependentSynthesis with
  retry logic, failed_agents tracking, variance-triggered rounds
- `session.py`: Session, SessionManager, BatchManager with file-based
  persistence and StepState tracking

### llm/

No changes needed. Already provider-agnostic.

- `LLMClient`: routes through LiteLLM (local, API key) or direct HTTP (OAuth)
- `CapabilityRegistry`: maps model IDs to capabilities and routing metadata
- `ProtoNeoSettings`: persisted to `~/.protoneo/settings.json`
- `providers/`: OAuth flows for OpenAI (active), Anthropic (disabled reference)

### knowledge/

Major restructuring. The monolithic `parser.py` splits into the
DocumentProcessor registry and individual parser strategy implementations.
Graph modules stay in the kernel but are parameterized.

**New files:**
- `types.py`: `DomainConfig`, `SeedEntity`, `SeedEdge`, `ParseResult`, `Parser` protocol
- `processor.py`: `DocumentProcessor` (registry + chain)
- `parsers/`: package of parser implementations (pymupdf, pdf2md, plaintext, markdown)
- `pipeline.py`: `GraphPipeline` orchestrator

**Renamed files:**
- `paper_ontology.py` → `ontology.py`
- `paper_graph.py` → `graph.py`
- `graph_extractor.py` → `extractor.py`
- `coref_resolver.py` → `coref.py`
- `graph_verifier.py` → `verifier.py`

**Deleted files:**
- `parser.py` (replaced by `processor.py` + `parsers/`)

### export/ (NEW)

Kernel export subsystem. Provides the `Exporter` protocol, the
`ExportRegistry`, built-in format exporters, and the API endpoint.

```
protoneo/export/
├── __init__.py             # create_export_registry() factory
├── types.py                # Exporter protocol, ExportRegistry
├── json_exporter.py        # Built-in: raw session JSON
└── markdown_exporter.py    # Built-in: generic deliberation Markdown
```

Applications register domain-specific exporters. The paper_review app
contributes `ReviewMarkdownExporter` and `ReviewPdfExporter` that render
its `ReviewPacket` schema into presentation formats.

### tools/

Restructured with `Tool` protocol and `ToolRegistry`.

```
protoneo/tools/
├── __init__.py             # create_tool_registry() factory
├── types.py                # Tool protocol, ToolResult, ToolRegistry
├── semantic_scholar.py     # SemanticScholarTool (wraps existing code)
└── web_search.py           # WebSearchTool (wraps existing code)
```

### config/

Extended with `AppManifest` and `AppRegistration`.

Applications never receive raw `FastAPI` access. They provide a pre-built
`APIRouter` and an optional `on_register` callback that receives a constrained
`AppRegistration` interface. This prevents apps from overriding kernel routes,
mutating shared state, or colliding with other apps.

```python
# config/schema.py

@dataclass
class AppRegistration:
    """Constrained interface provided to apps during kernel registration.

    Applications use this to contribute parsers, exporters, and tools
    to the kernel's registries. They cannot access the FastAPI app,
    app.state, or kernel internals directly.
    """

    def register_parser(self, parser: Parser, priority: int = 0) -> None: ...
    def register_exporter(self, exporter: Exporter) -> None: ...
    def register_tool(self, tool: Tool) -> None: ...


@dataclass
class AppManifest:
    """Contract between an application and the kernel."""

    name: str                           # "paper_review" (Python identifier)
    display_name: str                   # "Paper Review" (UI display)
    version: str                        # "0.1.0"
    description: str                    # One-line description

    # Routing: pre-built router, kernel mounts under /api/apps/{name}/
    router: APIRouter

    # Registration callback: receives constrained interface, not raw FastAPI
    on_register: Callable[[AppRegistration], None] | None = None

    # Domain knowledge (injected into kernel knowledge modules)
    domain_config: DomainConfig

    # Application directories
    profile_dir: Path | None = None     # Where venue/template profiles live
    prompt_dir: Path | None = None      # Where agent prompt templates live

    # Output rendering hints for the UI
    result_schema: dict | None = None   # JSON schema for ResultPacket display
    score_fields: list[dict] | None = None  # [{name, label, min, max}]
```

Applications build their router with routes relative to their namespace:

```python
# apps/paper_review/api.py
router = APIRouter()

@router.post("/preflight")              # Becomes /api/apps/paper_review/preflight
async def preflight(...): ...

@router.get("/conferences")             # Becomes /api/apps/paper_review/conferences
async def list_conferences(...): ...
```

The `on_register` callback contributes to kernel registries without touching
internals:

```python
# apps/paper_review/manifest.py
def _on_register(reg: AppRegistration):
    reg.register_exporter(ReviewMarkdownExporter())
    reg.register_exporter(ReviewPdfExporter())

manifest = AppManifest(
    ...,
    router=router,
    on_register=_on_register,
)
```

### api/

All graph and pipeline endpoints are kernel routes. Application routes live
under `/api/apps/{app_name}/`.

**app.py** factory with constrained registration:

```python
def create_app(config=None, apps: list[AppManifest] | None = None):
    app = FastAPI(title="ProtoNeo", version="0.1.0")

    # Initialize kernel subsystems (internal, not exposed to apps)
    doc_processor = create_document_processor()
    tool_registry = create_tool_registry()
    export_registry = create_export_registry()
    manifests: dict[str, AppManifest] = {}

    # Store on app.state for kernel routes only
    app.state.document_processor = doc_processor
    app.state.tool_registry = tool_registry
    app.state.export_registry = export_registry
    app.state.manifests = manifests

    # Register kernel routes
    register_kernel_routes(app, config)

    # Register applications through constrained interface
    for manifest in (apps or []):
        # Mount app router under /api/apps/{name}/
        app.include_router(
            manifest.router,
            prefix=f"/api/apps/{manifest.name}",
            tags=[manifest.display_name],
        )
        manifests[manifest.name] = manifest

        # App contributes to registries through constrained interface
        if manifest.on_register:
            reg = AppRegistration(
                _doc_processor=doc_processor,
                _tool_registry=tool_registry,
                _export_registry=export_registry,
            )
            manifest.on_register(reg)

    # Serve built UI if available
    ui_dist = Path(__file__).parent.parent.parent / "ui" / "dist"
    if ui_dist.exists():
        app.mount("/", StaticFiles(directory=ui_dist, html=True))

    return app
```

The kernel never passes `FastAPI` or `app.state` to application code. Apps
interact only through `APIRouter` (for routes) and `AppRegistration` (for
registries). An app cannot override kernel routes, access another app's state,
or mutate kernel internals directly.

**Kernel API surface:**

```
Health:
  GET  /api/health
  GET  /api/manifests                   # List all registered app manifests
  GET  /api/manifests/{app_name}        # Get specific app manifest

Settings:
  GET  /api/settings
  PUT  /api/settings
  GET  /api/settings/active-models
  GET  /api/presets
  POST /api/presets/{name}/activate

Models:
  GET  /api/models
  POST /api/models/discover
  POST /api/models/benchmark
  GET  /api/models/benchmark

Providers:
  GET  /api/providers
  GET  /api/providers/{name}
  POST /api/providers/login
  POST /api/providers/callback
  GET  /api/providers/{name}/login-status
  POST /api/providers/{name}/logout

Sessions:
  POST /api/sessions
  GET  /api/sessions
  GET  /api/sessions/{id}
  POST /api/sessions/{id}/upload
  POST /api/sessions/{id}/start
  POST /api/sessions/{id}/stop
  POST /api/sessions/{id}/retry
  WS   /api/sessions/{id}/stream

Graph:
  GET  /api/sessions/{id}/graph
  GET  /api/sessions/{id}/graph/step/{name}
  GET  /api/sessions/{id}/graph/export
  GET  /api/sessions/{id}/ontology
  POST /api/sessions/{id}/generate-ontology
  POST /api/sessions/{id}/extract-graph
  GET  /api/sessions/{id}/graph-utilization
  GET  /api/sessions/{id}/graph-summary

Pipeline:
  GET  /api/sessions/{id}/pipeline
  POST /api/sessions/{id}/pipeline/advance
  POST /api/sessions/{id}/pipeline/pause
  POST /api/sessions/{id}/pipeline/resume
  POST /api/sessions/{id}/pipeline/cancel
  POST /api/sessions/{id}/pipeline/step/{name}/run
  POST /api/sessions/{id}/pipeline/step/{name}/cancel
  POST /api/sessions/{id}/pipeline/edit-ontology

Export:
  GET  /api/export/formats              # List available export formats
  GET  /api/sessions/{id}/export?format=X

Parsers:
  GET  /api/parsers                     # List available parsers by extension

Tools:
  GET  /api/tools                       # List available tools

Agents:
  GET  /api/agents
```

---

## Knowledge Module Parameterization

The kernel owns the algorithms. The application injects domain expertise as
structured data via DomainConfig.

### DomainConfig

```python
# knowledge/types.py

@dataclass
class SeedEntity:
    """A domain-specific entity type seed."""
    name: str
    description: str
    attributes: list[dict] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)

@dataclass
class SeedEdge:
    """A domain-specific relationship type seed."""
    name: str
    description: str
    source_targets: list[dict] = field(default_factory=list)

@dataclass
class DomainConfig:
    """Domain expertise injected by an application into kernel knowledge
    modules. The kernel provides the algorithms. The application provides
    the prompts, seeds, and classification rules."""

    # Identity
    name: str                           # "academic_paper", "patent", "policy"

    # ── Ontology Generation ──
    base_entity_types: list[SeedEntity]
    base_edge_types: list[SeedEdge]
    fallback_entity_types: list[SeedEntity] = field(default_factory=list)
    structural_entity_types: list[SeedEntity] = field(default_factory=list)
    structural_edge_types: list[SeedEdge] = field(default_factory=list)
    domain_patterns: dict[str, list[dict]] = field(default_factory=dict)
    domain_keywords: dict[str, list[str]] = field(default_factory=dict)
    ontology_discovery_prompt: str = ""
    ontology_grounding_prompt: str = ""

    # ── Graph Extraction ──
    extraction_system_prompt: str = ""
    extraction_batch_size: int = 3

    # ── Verification ──
    verify_system_prompt: str = ""
    verify_connectivity_prompt: str = ""
    verify_completeness_prompt: str = ""
    verify_grounding_prompt: str = ""

    # ── Graph Presentation ──
    structural_node_types: set[str] = field(
        default_factory=lambda: {"Document", "Section"}
    )
    structural_edge_types_for_summary: set[str] = field(
        default_factory=lambda: {"HAS_SECTION", "CONTAINS", "APPEARS_IN"}
    )
    summary_max_chars: int = 3000
    summary_template: str = ""
```

### What Gets Extracted from Current Modules

**From `paper_ontology.py` → `apps/paper_review/domain/`:**

| Current location | Destination |
|------------------|-------------|
| `_BASE_ENTITY_TYPES` (7 types) | `domain/seeds.yaml` → `base_entity_types` |
| `_FALLBACK_ENTITY_TYPES` (2 types) | `domain/seeds.yaml` → `fallback_entity_types` |
| `_STRUCTURAL_ENTITY_TYPES` (1 type) | `domain/seeds.yaml` → `structural_entity_types` |
| `_BASE_EDGE_TYPES` (8 types) | `domain/seeds.yaml` → `base_edge_types` |
| `_STRUCTURAL_EDGE_TYPES` (4 types) | `domain/seeds.yaml` → `structural_edge_types` |
| `_DOMAIN_PATTERNS` (6 domains) | `domain/domain_patterns.yaml` → `domain_patterns` |
| `_DOMAIN_KEYWORDS` (6 keyword lists) | `domain/domain_patterns.yaml` → `domain_keywords` |
| Step 2 discovery prompt | `domain/prompts/ontology_discovery.md` |
| Step 3 grounding prompt | `domain/prompts/ontology_grounding.md` |

What stays in `ontology.py` (kernel): the 4-step workflow algorithm, the
self-consistency merging logic, the Pydantic models (renamed: `PaperOntology`
→ `Ontology`), `generate_ontology()`, `ontology_to_extraction_prompt()`.

**From `graph_verifier.py` → `apps/paper_review/domain/`:**

| Current location | Destination |
|------------------|-------------|
| `_VERIFY_SYSTEM` | `domain/config.yaml` → `verify_system_prompt` |
| `_VERIFY_CONNECTIVITY_PROMPT` | `domain/prompts/verify_connectivity.md` |
| `_VERIFY_COMPLETENESS_PROMPT` | `domain/prompts/verify_completeness.md` |
| `_VERIFY_GROUNDING_PROMPT` | `domain/prompts/verify_grounding.md` |

What stays in `verifier.py` (kernel): the 3-pass verification algorithm,
`VerificationResult`, node/edge manipulation logic, JSON parsing.

**From `graph_extractor.py`:**

The extraction system prompt is generated dynamically by
`ontology_to_extraction_prompt()` from the ontology. This stays in the kernel
(algorithmic). The app can override with `extraction_system_prompt` in
DomainConfig for domain-specific extraction guidance.

**From `paper_graph.py` → kernel `graph.py`:**

| Current | Kernel (generic) |
|---------|-----------------|
| `PaperGraph` | `KnowledgeGraph` |
| `to_reviewer_summary()` | `to_agent_briefing(domain_config)` |
| Hardcoded `_STRUCTURAL` set | Read from `domain_config.structural_node_types` |
| Hardcoded `_NON_SEMANTIC_RELS` set | Read from `domain_config.structural_edge_types_for_summary` |
| `compute_utilization(reviews)` | `compute_utilization(agent_outputs)` |
| Paper-specific summary sections | Generated from `domain_config.summary_template` |

### Prompt Template Format

All prompt templates use Python `str.format()` with named placeholders.
No template engine dependency.

Example `domain/prompts/verify_completeness.md`:

```markdown
Check this knowledge graph for completeness against the full document text.

## Current Graph Entities:
{entity_summary}

## Document Text:
{document_text}

Identify concrete, named entities discussed in the document that are MISSING
from the graph. Focus on:
{focus_areas}

Do NOT suggest vague concepts. Every suggestion must be a specific named
thing from the document.

Return JSON:
{{
  "missing_concepts": [
    {{"concept": "exact name", "suggested_type": "type", "section": "where",
      "evidence": "quote or paraphrase"}}
  ]
}}
```

### Graph Pipeline Orchestrator and Pipeline Handoff Contract

The end-to-end pipeline spans two layers: the kernel owns the graph pipeline,
the application owns the domain-specific stages (e.g., review deliberation).
A failure after the graph pipeline completes must not strand the session or
re-run non-idempotent stages against stale state. This requires a durable
handoff contract with checkpoints and idempotent retry semantics.

**Stage checkpoints**: Every pipeline stage (kernel or app) writes a durable
checkpoint to the session before the next stage begins. The checkpoint records
the stage name, completion time, and a key referencing the stage's output in
the session. On resume, the pipeline skips all stages whose checkpoints
already exist.

```python
# deliberation/session.py addition

@dataclass
class StageCheckpoint:
    """Durable record of a completed pipeline stage."""
    stage_name: str
    completed_at: str                   # ISO timestamp
    output_key: str                     # Where output is stored in session
    idempotent: bool = True             # Safe to re-run?

class Session(BaseModel):
    ...
    checkpoints: list[StageCheckpoint] = []
    last_checkpoint: str = ""           # Last completed stage name
```

**Pipeline stage registry**: The kernel defines the graph stages. The
application appends its stages during manifest registration. The combined
sequence is the session's full pipeline.

```python
# Kernel graph stages (always present)
KERNEL_STAGES = [
    "metadata", "ontology", "extraction", "coref", "verification", "summary"
]

# Application declares its stages in the manifest
class AppManifest:
    ...
    pipeline_stages: list[str] = field(default_factory=list)
    # e.g., ["independent_review", "deliberation", "meta_review", "pc_chair"]
```

The full pipeline for a session is `KERNEL_STAGES + manifest.pipeline_stages`.
`PipelineControl` gates, pause, resume, and cancel apply to the full sequence.

**Idempotent retry**: Each stage checks for an existing checkpoint before
executing. If the checkpoint exists and the stage is marked idempotent, it
reads the cached output and skips execution. Non-idempotent stages (e.g.,
deliberation with temperature > 0) are flagged and require explicit
confirmation to re-run.

**Failure and recovery**: If the kernel graph pipeline fails at stage N, the
session records the error and stops. On retry, stages 1..N-1 are skipped
(checkpoints exist), and stage N is re-attempted. If the application's review
stage fails, the same logic applies: the graph pipeline checkpoints are
preserved, and only the failed app stage is re-attempted.

**Cancel**: Stops execution at the current stage. Session status becomes
`STOPPED`. Completed checkpoints are preserved. Resume picks up from the
last checkpoint.

`protoneo/knowledge/pipeline.py`:

```python
class GraphPipeline:
    """Orchestrates knowledge graph generation from a document.

    Runs: metadata → ontology → extract → coref → verify → summarize.
    The algorithm is kernel-owned. Domain expertise comes from DomainConfig.
    Each step writes a durable checkpoint to the session.
    """

    def __init__(self, llm_client, session_manager, domain_config):
        self.llm = llm_client
        self.sessions = session_manager
        self.domain = domain_config

    async def run(
        self,
        session_id: str,
        document: Document,
        bus: SessionEventBus,
        ctl: PipelineControl,
        model: str | None = None,
    ) -> KnowledgeGraph:
        """Run the 6-step graph pipeline with checkpoint-based resume.

        For each step:
        1. Check if checkpoint exists (skip if so)
        2. Check PipelineControl gates (pause if gated)
        3. Execute the step
        4. Write checkpoint to session
        5. Emit bus event
        """
```

The application's pipeline code follows the same checkpoint protocol:

```python
# apps/paper_review/pipeline.py

async def run_review_stages(session_id, ...):
    """Run review stages with checkpoint-based resume.

    Stages: independent_review → deliberation → meta_review → pc_chair
    Each stage checks for existing checkpoint before executing.
    Calls kernel deliberation engine, does not reimplement it.
    """
```

This absorbs ~500 lines of graph pipeline orchestration from the application.
The application's pipeline shrinks to ~300 lines of review-specific wiring.

---

## Session Model

Rename paper-specific fields to generic names. Add app ownership and schema
versioning so the kernel can resolve the correct manifest, exporters, and
domain config for any stored session, even when multiple apps are registered.

```python
class Session(BaseModel):
    session_id: str
    schema_version: int = 1             # Bump on breaking field changes
    status: SessionStatus
    created_at: datetime
    config: dict = {}

    # App ownership (set at session creation, immutable)
    app_name: str = ""                  # Which app created this session
    app_version: str = ""               # App version at creation time

    # Document (generic)
    document_text: str = ""             # Was: paper_text
    document_markdown: str = ""         # Was: paper_markdown

    # Knowledge graph (generic)
    knowledge_graph: dict | None = None # Was: paper_graph

    # Pipeline (kernel + app stages combined)
    pipeline_steps: dict = {}
    current_stage: str = ""
    last_checkpoint: str = ""           # Last completed stage name

    # Deliberation output
    result: dict | None = None
    error: str | None = None

    # Application-specific storage
    app_data: dict = {}                 # Apps store domain data here
```

**App ownership**: When a session is created through an application endpoint
(e.g., `POST /api/apps/paper_review/start-review`), the kernel sets
`app_name="paper_review"` and `app_version` from the manifest. Kernel routes
like `/api/sessions/{id}/export` resolve the owning app's manifest to find
the correct exporters, domain config, and result schema. The singular
`/api/manifests` endpoint lists all registered apps; session-specific
operations always resolve by `session.app_name`.

**Schema versioning**: `schema_version` starts at 1. When a future release
changes the Session schema (renames fields, changes types), the version bumps
and a migrator converts old sessions to the new format. This prevents the
"existing sessions become unreadable" problem across releases.

The `app_data` field gives applications a namespace for domain-specific state.
For paper_review: `{"conference": "hpdc26", "review_packet": {...},
"pc_chair_review": {...}, "prompt_pack_version": "1.0"}`.

---

## Migration Strategy

This is a clean-slate v0.1.0 release with no existing users. However, the
codebase has dev session data on disk and the 8-session rollout transforms
the codebase incrementally. Two concerns must be addressed: dev data survival
during the rollout, and schema stability for the public release.

### During the 8-Session Rollout

Session 1 (directory restructure) wipes all dev session data in
`backend/data/sessions/`. This is acceptable because:
- No external users exist
- The data is development artifacts from prior testing
- The session schema changes (field renames) would invalidate it anyway

After Session 1, the slate is clean. Subsequent sessions build on the new
schema without dual-read or compatibility shims.

### For the Public Release (v0.1.0+)

The `schema_version` field on `Session` (starting at 1) enables forward
migration. When v0.2.0 changes the session schema:

1. Bump `schema_version` to 2
2. Ship a migrator: `protoneo migrate` reads all session JSON files, detects
   `schema_version < 2`, transforms fields, writes back
3. The kernel reads sessions with a version check: if `schema_version` is
   older than current, either auto-migrate in memory or reject with a clear
   error pointing to `protoneo migrate`

No dual-read/write windows, no deprecated field aliases. The migrator runs
once and the old format is gone. This is acceptable for a team tool where
the operator controls when to upgrade.

### Route Stability

Application routes are namespaced under `/api/apps/{app_name}/`. Kernel
routes are under `/api/`. Neither changes when a new app is added. The
namespace prevents collisions between apps and between apps and kernel.

If a kernel route must be renamed in a future release, the old path is
preserved as a redirect for one minor version, then removed.

---

## Application Contract

### What an Application Provides

1. **AppManifest**: name, display_name, router, on_register, domain_config
2. **DomainConfig**: seeds, prompts, patterns (YAML + Markdown files)
3. **API routes**: domain-specific endpoints mounted under `/api/app/`
4. **Exporters**: domain-specific format renderers (registered into kernel)
5. **Pipeline logic**: how to use kernel deliberation for the domain
6. **Output schemas**: Pydantic models for domain-specific results
7. **Profiles**: venue/template YAML configs
8. **Prompt templates**: agent system prompts for domain roles

### Paper Review Application Routes

All mounted under `/api/apps/paper_review/` by the kernel (the app's router
defines paths relative to its namespace):

```
POST /api/apps/paper_review/preflight
POST /api/apps/paper_review/start-review
POST /api/apps/paper_review/batch-review
GET  /api/apps/paper_review/batch/{id}
GET  /api/apps/paper_review/batches
GET  /api/apps/paper_review/conferences
GET  /api/apps/paper_review/conferences/{slug}

POST /api/apps/paper_review/sessions/{id}/launch-review
POST /api/apps/paper_review/sessions/{id}/refine-field
POST /api/apps/paper_review/sessions/{id}/update-final-review
```

A hypothetical `patent_review` app would mount under
`/api/apps/patent_review/` with zero collision risk.

### Paper Review Application Files

| File | Lines (est.) | Responsibility |
|------|-------------|----------------|
| `manifest.py` | 80 | AppManifest + DomainConfig loading |
| `api.py` | 400 | ~12 domain routes (down from 2,027) |
| `pipeline.py` | 300 | Review stage orchestration (down from 908) |
| `review.py` | 490 | Agent config building, output parsing |
| `schemas.py` | 94 | ReviewPacket, IndividualReview, MetaReview |
| `conference.py` | 133 | Profile loading |
| `preflight.py` | 229 | Manuscript checks |
| `exporters.py` | 400 | ReviewMarkdownExporter, ReviewPdfExporter |
| `prompts.py` | 63 | Reviewer prompt assembly |

Total application Python: ~2,189 lines.
Former `export.py` becomes `exporters.py` (registers into kernel export system).

---

## Naming Changes

### Python Classes

| Current | New |
|---------|-----|
| `PaperOntology` | `Ontology` |
| `OntologyEntityType` | `EntityType` |
| `OntologyEdgeType` | `EdgeType` |
| `PaperGraph` | `KnowledgeGraph` |
| `PaperMetadata` | `DocumentMetadata` |
| `to_reviewer_summary()` | `to_agent_briefing(domain_config)` |
| `compute_utilization(reviews)` | `compute_utilization(agent_outputs)` |
| `generate_paper_ontology()` | `generate_ontology(domain_config, ...)` |
| `extract_paper_graph()` | `extract_graph(domain_config, ...)` |

### Files

| Current | New |
|---------|-----|
| `paper_ontology.py` | `ontology.py` |
| `paper_graph.py` | `graph.py` |
| `graph_extractor.py` | `extractor.py` |
| `coref_resolver.py` | `coref.py` |
| `graph_verifier.py` | `verifier.py` |
| `parser.py` | `processor.py` + `parsers/` package |
| `test_pc_panel.py` | `test_paper_review.py` |

### Strings and Branding

| Current | New |
|---------|-----|
| "PC Panel" (UI text) | Read from `manifest.display_name` |
| "pc_panel" (imports) | "paper_review" |
| "paper_text" (session field) | "document_text" |
| "paper_markdown" (session field) | "document_markdown" |
| "paper_graph" (session field) | "knowledge_graph" |
| `/api/panel/*` (routes) | `/api/apps/paper_review/*` |
| `applications/pc_panel/` (directory) | `apps/paper_review/` |
| `.pc-*` CSS classes | `.card-*` |

---

## Dependencies

### Remove

| Dependency | Reason |
|------------|--------|
| `docling>=2.0.0` | Replaced by pdf2md subprocess |
| `curl-cffi>=0.7.0` | Was a docling dependency |

### Keep

```toml
[project]
name = "protoneo"
version = "0.1.0"
description = "Composable LLM deliberation runtime"
license = "AGPL-3.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "websockets>=13.0",
    "python-multipart>=0.0.9",
    "pydantic>=2.0.0",
    "python-dotenv>=1.0.0",
    "litellm>=1.50.0",
    "openai>=1.0.0",
    "httpx>=0.27.0",
    "PyMuPDF>=1.24.0",
    "charset-normalizer>=3.0.0",
    "chardet>=5.0.0",
    "pyyaml>=6.0.0",
    "markdown>=3.0.0",
    "weasyprint>=62.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
]

[project.scripts]
protoneo = "protoneo.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["protoneo", "apps"]
```

---

## CLI and Packaging

### Entry Point

```python
# protoneo/cli.py

def main():
    """Boot ProtoNeo kernel with registered applications."""
    import uvicorn
    from protoneo.api.app import create_app
    from apps.paper_review import manifest as paper_review_manifest

    app = create_app(apps=[paper_review_manifest])
    uvicorn.run(app, host="0.0.0.0", port=5002)
```

### Frontend Build

Built UI is served by FastAPI as static files:

```bash
cd ui && npm install && npm run build    # -> ui/dist/
```

`create_app()` mounts `ui/dist/` if it exists. In development, Vite dev
server (port 3000) proxies `/api/*` to the kernel (port 5002).

### Single Install Goal (Post v0.1.0)

Eventually: `pip install protoneo` includes pre-built frontend assets.
`protoneo` command starts the server and opens a browser. For v0.1.0,
the two-step install (pip + npm) is acceptable.

---

## Coding Sessions

### Session 1: Directory Restructure

Move files to the new layout. Update all imports. No logic changes.

1. Create `protoneo/` at repo root (move from `backend/protoneo/`)
2. Create `apps/paper_review/` (move from `backend/applications/pc_panel/`)
3. Move `frontend/` → `ui/`
4. Move `backend/tests/` → `tests/`
5. Move `backend/pyproject.toml` → `pyproject.toml` (update package paths)
6. Move `backend/run_kernel.py` → `run.py`
7. Wipe `backend/data/sessions/` (dev data, incompatible with new schema)
8. Update every import path
9. Rename `test_pc_panel.py` → `test_paper_review.py`
10. Delete `backend/` directory (now empty)
11. Verify: `uv run pytest tests/ -q` passes, `cd ui && npm run build` works

### Session 2: Extract Kernel Infrastructure + Registries

Build the subsystem scaffolding.

1. Extract `SessionEventBus` → `api/events.py`
2. Extract `PipelineControl` → `api/pipeline_control.py`
3. Create `knowledge/types.py` with `ParseResult`, `Parser` protocol,
   `DomainConfig`, `SeedEntity`, `SeedEdge`
4. Create `knowledge/processor.py` with `DocumentProcessor`
5. Create `knowledge/parsers/` package with 4 built-in parsers
   (extract code from current `parser.py` into strategy implementations)
6. Create `knowledge/__init__.py` with `create_document_processor()`
7. Create `tools/types.py` with `Tool` protocol, `ToolResult`, `ToolRegistry`
8. Wrap `semantic_scholar.py` and `web_search.py` to implement `Tool` protocol
9. Create `tools/__init__.py` with `create_tool_registry()`
10. Create `export/types.py` with `Exporter` protocol, `ExportRegistry`
11. Create `export/json_exporter.py` and `export/markdown_exporter.py`
12. Create `export/__init__.py` with `create_export_registry()`
13. Add `AppManifest`, `AppRegistration` dataclasses to `config/schema.py`
14. Update `app.py`: initialize registries, mount app routers under
    `/api/apps/{name}/`, wire `on_register` through `AppRegistration`
15. Verify: all tests pass, backend starts, app routes respond under
    `/api/apps/paper_review/`

### Session 3: Knowledge Module Parameterization

Extract domain data from kernel modules.

1. Create `apps/paper_review/domain/` directory structure
2. Extract all seed data from `paper_ontology.py` → `domain/seeds.yaml`
3. Extract domain patterns and keywords → `domain/domain_patterns.yaml`
4. Extract ontology LLM prompts → `domain/prompts/ontology_*.md`
5. Extract verification prompts → `domain/prompts/verify_*.md`
6. Create `domain/config.yaml` with structural types, summary settings
7. Refactor `ontology.py`: `generate_ontology(domain_config, ...)` reads
   seeds and prompts from DomainConfig
8. Refactor `verifier.py`: `verify_graph(domain_config, ...)` reads prompts
   from DomainConfig
9. Refactor `extractor.py`: accept DomainConfig for extraction overrides
10. Implement `_load_domain()` in `apps/paper_review/manifest.py`
11. Verify: ontology generation quality unchanged on test document

### Session 4: Rename Classes and Session Model

Apply all naming changes.

1. `PaperOntology` → `Ontology`, `PaperGraph` → `KnowledgeGraph`,
   `PaperMetadata` → `DocumentMetadata` (classes and all references)
2. `paper_ontology.py` → `ontology.py`, `paper_graph.py` → `graph.py`
   (file renames, already in new directory from Session 1)
3. `to_reviewer_summary()` → `to_agent_briefing(domain_config)`
4. `generate_paper_ontology()` → `generate_ontology()`
5. `extract_paper_graph()` → `extract_graph()`
6. Session fields: `paper_text` → `document_text`, `paper_markdown` →
   `document_markdown`, `paper_graph` → `knowledge_graph`
7. Add new Session fields: `app_name`, `app_version`, `schema_version`,
   `last_checkpoint`, `checkpoints`, `app_data`
8. Ensure session creation sets `app_name` from the requesting app's manifest
9. Update all field references across kernel and application
10. Verify: all tests pass, session JSON includes app ownership fields

### Session 5: Move Graph and Pipeline Routes to Kernel

Consolidate the API surface.

1. Move graph endpoints from application `api.py` to kernel `routes.py`
2. Move pipeline endpoints from application `api.py` to kernel `routes.py`
3. Add export endpoints: `GET /api/export/formats`,
   `GET /api/sessions/{id}/export?format=X`
4. Add parser listing: `GET /api/parsers`
5. Add tool listing: `GET /api/tools`
6. Update application `api.py`: remove moved routes, convert remaining
   routes to `APIRouter` (paths relative to app namespace, kernel mounts
   under `/api/apps/paper_review/`)
7. Move `export.py` from application → `apps/paper_review/exporters.py`,
   wrap in Exporter protocol, register via `on_register` callback
8. Ensure export endpoint resolves app ownership from `session.app_name`
8. Update `ui/src/api/kernel.js` with new endpoint paths
9. Verify: all API calls work, graph visualization loads

### Session 6: Graph Pipeline Orchestrator + Checkpoint Contract

Create the kernel-level pipeline with durable checkpoints.

1. Add `StageCheckpoint` model and `checkpoints` list to Session
2. Create `protoneo/knowledge/pipeline.py` with `GraphPipeline` class
3. Implement checkpoint-based resume: each step checks for existing
   checkpoint before executing, writes checkpoint on completion
4. Move graph pipeline logic from application's `pipeline.py`: NLP prepass,
   ontology, extraction, coref, verification, summarization, session state,
   events
5. `GraphPipeline.__init__` takes `llm_client`, `session_manager`,
   `domain_config`; `run()` takes `session_id`, `document`, `bus`, `ctl`
6. Application's `pipeline.py` follows the same checkpoint protocol for
   review stages (independent_review, deliberation, meta_review, pc_chair)
7. `PipelineControl` pause/resume/cancel applies to the full stage sequence
   (kernel stages + app stages registered via `manifest.pipeline_stages`)
8. Application keeps only: review stage orchestration, agent config building,
   deliberation invocation, output parsing, PC Chair
9. Verify: end-to-end pipeline runs with checkpoints, resume after
   simulated failure skips completed stages, graph quality unchanged

### Session 7: UI Cleanup

Rename components, remove hardcoded branding, add manifest awareness.

1. Rename Vue files per naming table
2. Update router paths and component imports
3. `App.vue`: fetch `/api/manifest` on mount, use `display_name` for branding
4. `Home.vue`: populate profile dropdown from manifest data
5. `ResultPacket.vue`: read `score_fields` from manifest for score rendering
6. Add export format dropdown from `/api/export/formats`
7. Add parser selection from `/api/parsers` (optional, power users)
8. Remove "PC Panel" text from all components
9. Rename `.pc-*` CSS classes to `.card-*`
10. Verify: UI loads, branding shows app name, all features work

### Session 8: Tests, Docs, Polish

1. Update test imports and assertions for all renames
2. Add tests: `DocumentProcessor` parser registry and fallback chain
3. Add tests: `ExportRegistry` format registration and lookup
4. Add tests: `ToolRegistry` registration and availability
5. Add tests: `DomainConfig` loading and validation
6. Add tests: `AppManifest` registration
7. Add tests: `GraphPipeline` orchestration
8. Write `docs/kernel.md`: capabilities, extension points, API reference
9. Write `docs/building-apps.md`: step-by-step app creation guide
10. Write `docs/paper-review.md`: usage, profiles, prompt customization
11. Update `CLAUDE.md` and `README.md`
12. Create `protoneo/cli.py` entry point
13. Clean `pyproject.toml`: remove legacy deps, update package discovery
14. Delete planning files: `PLAN.md`, `PIPELINE-FIXES.md`, `SESSION_PROMPTS.md`
15. Final: `pytest tests/ -q`, `cd ui && npm run build`, `protoneo`

---

## Test Strategy

### Kernel Tests (~110+ tests, `test_kernel.py`)

All existing tests survive with import/name updates. New tests added:

- `DocumentProcessor`: parser registration, priority ordering, fallback chain,
  availability filtering, preferred parser selection, post-processor pipeline
- `ExportRegistry`: format registration, lookup, available_formats listing,
  built-in JSON/Markdown exporters produce valid output
- `ToolRegistry`: registration, availability checking, tool listing
- `DomainConfig`: loading from YAML, missing field validation, prompt template
  placeholder verification
- `AppManifest`: router mounting under `/api/apps/{name}/`, `AppRegistration`
  constrained interface, exporter merging, manifest endpoint
- `GraphPipeline`: step sequencing, checkpoint writing, checkpoint-based
  resume (skip completed stages), error handling, gate checks, event emission
- `StageCheckpoint`: creation, persistence, resume logic, idempotency flags
- `Session.app_name`: set on creation, used for manifest/exporter resolution
- `KnowledgeGraph.to_agent_briefing(domain_config)`: with different configs

### Application Tests (~25 tests, `test_paper_review.py`)

All existing PC Panel tests survive with import/name updates:
- Conference profile loading and validation
- Prompt template loading and assembly
- Agent config building from profiles
- Review output parsing (JSON extraction, fence stripping)
- Meta-review parsing and score validation
- ReviewPacket construction from deliberation results
- Preflight checks
- ReviewMarkdownExporter and ReviewPdfExporter output

### OAuth Tests (`test_oauth.py`)

Unchanged. PKCE generation, credential storage, provider auth URLs.

---

## Success Criteria

The restructuring is complete when:

1. `protoneo/` contains zero application-specific imports, prompts, or data
2. `apps/paper_review/` contains zero kernel algorithm implementations
3. `apps/paper_review/domain/` contains ALL paper-specific seeds and prompts
4. Kernel knowledge modules accept `DomainConfig` for all LLM prompts
5. `create_app()` has zero hardcoded application imports
6. Applications interact with kernel only through `APIRouter` and
   `AppRegistration` (no raw `FastAPI` or `app.state` access)
7. Sessions carry `app_name` and `schema_version` from creation
8. Export resolves the owning app from `session.app_name`
9. Pipeline stages write durable checkpoints; resume skips completed stages
10. Document processing uses `DocumentProcessor` registry with fallback chain
11. Export uses `ExportRegistry` with kernel built-ins + app-contributed formats
12. Tools use `ToolRegistry` with availability checking
13. UI reads branding, score fields, and export formats from `/api/manifests`
14. All ~145 tests pass
15. `cd ui && npm run build` succeeds
16. End-to-end: upload PDF → graph pipeline → deliberation → export
17. Pipeline resume: kill mid-pipeline, restart, resumes from last checkpoint
18. A developer can read `docs/building-apps.md` and create a new application
    without reading kernel internals
