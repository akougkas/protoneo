# ProtoNeo

ProtoNeo is a multi-agent deliberation kernel for structured LLM workflows. It coordinates agent panels through patterns like parallel review, sequential refinement, round-robin challenge, and independent synthesis, with session persistence and real-time streaming.

The main application in this repository is **PC Panel**: a pre-submission review tool for academic authors. You upload a manuscript PDF, choose a target venue, and receive a review packet shaped by that conference's criteria, reviewer roles, and scoring scale.

## What PC Panel Does

- Builds a paper knowledge graph from the uploaded manuscript
- Runs a conference-specific panel of reviewer agents in parallel
- Orchestrates multi-round deliberation where reviewers challenge each other
- Produces a final review packet with scores, strengths, weaknesses, and a prioritized revision plan

Conference profiles drive the workflow. Different venues can define different reviewer roles, prompts, scales, and evaluation criteria.

## Stack

- Backend: FastAPI + WebSocket streaming
- Frontend: Vue 3 + Vite + D3
- Python environment: `uv`
- LLM routing: local endpoints, API-key providers, and OAuth-backed subscriptions

## Requirements

- Python `>=3.12`
- Node.js `>=18`
- `uv`
- At least one configured LLM provider

## Quickstart

1. Install dependencies:

```bash
npm install
cd frontend && npm install
cd ../backend && uv sync
cd ..
```

2. Create a local environment file:

```bash
cp .env.example .env
```

3. Edit `.env` with any provider credentials you want available at startup.

4. Start the backend:

```bash
npm run kernel
```

5. In a second terminal, start the frontend:

```bash
npm run frontend
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

ProtoNeo requires at least one model provider before reviews can run.

Start from the example file:

```bash
cp .env.example .env
```

The example includes placeholders for:

- OpenAI-compatible local endpoints
- OpenRouter
- Anthropic
- OpenAI
- Google / Gemini
- Optional reviewer tooling such as Semantic Scholar, Brave, and SearxNG

## Running Tests

```bash
cd backend
uv run pytest tests/ -q
```

## Project Layout

```text
backend/
  protoneo/          # Kernel: agents, deliberation, API, knowledge graph, LLM routing
  applications/
    pc_panel/        # PC Panel: review orchestration, conference profiles, prompts
  tests/
frontend/
  src/               # Vue application and graph/review UI
```

## Development Notes

- Backend runs on `http://localhost:5002`
- Frontend runs on `http://localhost:3000`
- Frontend API requests proxy to the backend during local development
