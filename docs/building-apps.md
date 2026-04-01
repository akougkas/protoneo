# Building Applications on ProtoNeo

ProtoNeo applications are domain specializations of the kernel's deliberation capabilities. An application provides domain-specific prompts, entity/edge seeds, output schemas, pipeline stages, and API routes. The kernel provides all intelligence, wiring, and infrastructure.

## Quick Start

Create a new application directory under `apps/`:

```
apps/
  my_app/
    __init__.py
    manifest.py       # AppManifest + DomainConfig
    api.py            # APIRouter with domain routes
    pipeline.py       # Domain-specific pipeline stages
    schemas.py        # Output data models
    exporters.py      # Custom export formats
    domain/
      config.yaml     # Domain settings
      seeds.yaml      # Entity/edge type seeds
      prompts/        # LLM prompt templates
    profiles/         # Venue/template configurations (optional)
    prompts/          # Agent prompt templates (optional)
```

## Step 1: Define Your Domain Config

The `DomainConfig` tells the kernel what entity types, edge types, and prompts to use for your domain.

Create `domain/seeds.yaml`:

```yaml
base_entity_types:
  - name: Claim
    description: "A factual claim or assertion"
    attributes:
      - name: confidence
        type: float
    examples:
      - "GDP grew by 3.2% in Q4"

base_edge_types:
  - name: SUPPORTS
    description: "Evidence supporting a claim"
    source_targets:
      - source: Evidence
        target: Claim
```

Create `domain/config.yaml`:

```yaml
name: my_domain
structural_node_types: ["Document", "Section"]
structural_edge_types_for_summary: ["HAS_SECTION", "CONTAINS"]
summary_max_chars: 3000
```

Create prompt templates in `domain/prompts/` as Markdown files with `{placeholder}` variables for Python `str.format()`.

## Step 2: Create the Manifest

```python
# apps/my_app/manifest.py

from pathlib import Path
import yaml
from protoneo.config.schema import AppManifest
from protoneo.knowledge.types import DomainConfig, SeedEntity, SeedEdge

_DOMAIN_DIR = Path(__file__).resolve().parent / "domain"


def _load_domain() -> DomainConfig:
    seeds = yaml.safe_load((_DOMAIN_DIR / "seeds.yaml").read_text())
    config = yaml.safe_load((_DOMAIN_DIR / "config.yaml").read_text())

    return DomainConfig(
        name=config["name"],
        base_entity_types=[
            SeedEntity(name=e["name"], description=e["description"],
                       attributes=e.get("attributes", []),
                       examples=e.get("examples", []))
            for e in seeds.get("base_entity_types", [])
        ],
        base_edge_types=[
            SeedEdge(name=e["name"], description=e["description"],
                     source_targets=e.get("source_targets", []))
            for e in seeds.get("base_edge_types", [])
        ],
    )


domain_config = _load_domain()


def _on_register(reg):
    from .exporters import MyExporter
    reg.register_exporter(MyExporter())


def _get_router():
    from .api import router
    return router


manifest = AppManifest(
    name="my_app",
    display_name="My Application",
    version="0.1.0",
    description="Description of what this app does",
    router=_get_router(),
    on_register=_on_register,
    domain_config=domain_config,
    pipeline_stages=["analysis", "synthesis"],
)
```

## Step 3: Define API Routes

Use FastAPI's `APIRouter`. Paths are relative to the app namespace. The kernel mounts them under `/api/apps/{name}/`.

```python
# apps/my_app/api.py

from fastapi import APIRouter

router = APIRouter()

@router.get("/status")              # Becomes /api/apps/my_app/status
async def get_status():
    return {"status": "ready"}

@router.post("/analyze")            # Becomes /api/apps/my_app/analyze
async def start_analysis(...):
    ...
```

Access kernel services through the routes module:

```python
from protoneo.api.routes import (
    get_session_manager,
    get_llm_client,
    get_event_buses,
    get_pipeline_controls,
)
```

## Step 4: Implement Pipeline Stages

Your pipeline stages follow the same checkpoint protocol as kernel stages. Use the kernel's `DeliberationEngine` for agent orchestration.

```python
# apps/my_app/pipeline.py

from protoneo.deliberation.engine import DeliberationEngine
from protoneo.deliberation.session import StageCheckpoint

async def run_analysis_stages(session_id, ...):
    session = await session_manager.get(session_id)

    # Check checkpoint before running
    if not any(cp.stage_name == "analysis" for cp in session.checkpoints):
        # Run your analysis
        result = await engine.deliberate(context, config)

        # Write checkpoint
        session.checkpoints.append(StageCheckpoint(
            stage_name="analysis",
            completed_at=datetime.now(timezone.utc).isoformat(),
            output_key="app_data.analysis_result",
        ))
        session.app_data["analysis_result"] = result
        await session_manager.update(session)
```

## Step 5: Create Custom Exporters

Implement the `Exporter` protocol to add domain-specific export formats:

```python
# apps/my_app/exporters.py

class MyExporter:
    @property
    def format_name(self) -> str: return "my-format"

    @property
    def mime_type(self) -> str: return "text/markdown"

    @property
    def file_extension(self) -> str: return ".md"

    async def export(self, session, app_data=None) -> bytes:
        # Render your domain-specific output
        return formatted_output.encode("utf-8")
```

Register exporters in the `on_register` callback (see Step 2).

## Step 6: Register Your App

Add your manifest to the app list in `run.py`:

```python
from apps.my_app.manifest import manifest as my_app_manifest

app = create_app(config, apps=[paper_review_manifest, my_app_manifest])
```

## Constraints

Applications must follow these rules:

1. **No kernel imports from apps.** The kernel never imports from `apps.*`.
2. **No raw FastAPI access.** Apps receive an `APIRouter`, not the `FastAPI` instance.
3. **No direct `app.state` access.** Use `AppRegistration` to contribute to registries.
4. **No kernel algorithm reimplementation.** Use the kernel's deliberation engine, graph pipeline, and export system.
5. **Domain data stays in the app.** Entity seeds, prompts, venue profiles, and output schemas belong in `apps/{name}/domain/`.

## Testing

Write tests for your application in `tests/test_{app_name}.py`. Mock the LLM client and test:

- Domain config loading
- Agent config building
- Output parsing
- Pipeline stage sequencing
- Export formatting

Run with: `uv run pytest tests/ -q`
