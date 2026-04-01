# ProtoNeo

ProtoNeo is a composable runtime for building applications that require LLM councils. It provides agent primitives, deliberation patterns, knowledge graph generation, multi-provider LLM routing, session management, a real-time event system, pluggable document processing, structured export, and a browser-based dashboard.

The paradigm is Linux kernel + distros. ProtoNeo is the kernel. Applications are opinionated distros built on top. The kernel provides all intelligence, wiring, and infrastructure. Applications bring domain-specific prompts, ontology seeds, output schemas, and pipeline configurations.

## First Application: Paper Review

An AI peer review panel for academic papers. Authors upload a PDF, the kernel builds a knowledge graph, runs a panel of reviewer agents through structured deliberation, and produces a review packet with scores, strengths, weaknesses, and revision guidance. Targets systems conferences (HPDC, SC) for pre-submission self-assessment.

## Stack

- **Backend:** FastAPI + WebSocket streaming
- **Frontend:** Vue 3 + Vite + D3
- **Python environment:** `uv`
- **LLM routing:** local endpoints, API-key providers, and OAuth-backed subscriptions

## Requirements

- Python `>=3.12`
- Node.js `>=18`
- `uv`
- At least one configured LLM provider

## Quickstart

1. Install dependencies:

```bash
uv sync
cd ui && npm install && cd ..
```

2. Create a local environment file:

```bash
cp .env.example .env
```

3. Edit `.env` with any provider credentials you want available at startup.

4. Start the backend:

```bash
uv run python run.py
```

5. In a second terminal, start the frontend dev server:

```bash
cd ui && npx vite
```

6. Open `http://localhost:3000`.

## First Launch

ProtoNeo does not ship with a default active model. After the UI opens:

1. Go to **Settings**
2. Connect or enable providers
3. Run model discovery or refresh providers
4. Select an active model for each provider you want to use

Local providers such as LM Studio and Ollama are supported, along with API-key and subscription-backed providers.

## Configuration

Start from the example file:

```bash
cp .env.example .env
```

The example includes placeholders for OpenAI-compatible local endpoints, OpenRouter, Anthropic, OpenAI, and optional tooling (Semantic Scholar, Brave, SearxNG).

## Running Tests

```bash
uv run pytest tests/ -q
```

229 tests: 149 kernel, 19 OAuth, 61 paper review. All tests mock the LLM client and run without network access.

## Project Layout

```text
protoneo/              Kernel: agents, deliberation, knowledge, LLM routing, API, export, tools
apps/
  paper_review/        Paper Review: conference profiles, review pipeline, prompts, exporters
ui/
  src/                 Vue 3 dashboard with graph visualization and review UI
tests/                 Kernel and application tests
docs/                  Documentation (kernel, building apps, paper review)
```

## Documentation

- [Kernel capabilities and API reference](docs/kernel.md)
- [Building applications on ProtoNeo](docs/building-apps.md)
- [Paper Review usage and configuration](docs/paper-review.md)

## Development

- Backend runs on `http://localhost:5002`
- Frontend dev server runs on `http://localhost:3000` and proxies `/api/*` to the backend
- Build the frontend for production: `cd ui && npx vite build`
- The kernel serves built assets from `ui/dist/` when available

## License

AGPL-3.0
