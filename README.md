# ProtoNeo

Multi-agent deliberation kernel. Orchestrates LLM agents through structured deliberation patterns (parallel, sequential, round-robin, independent synthesis) with real-time WebSocket streaming, multi-provider routing, and session persistence.

## PC Panel

Pre-submission review tool for academic authors. Upload a manuscript PDF, select a target conference, and receive a structured review packet from a simulated program committee. The value is straightforward: a better submission results from catching structural issues, missing baselines, weak scaling arguments, and scope misalignment before the real review process begins.

The pipeline builds a paper knowledge graph, runs parallel reviewer agents calibrated to the venue's criteria, orchestrates a multi-round deliberation where reviewers challenge and refine each other's assessments, and synthesizes a final review packet with scores, strengths, weaknesses, and a prioritized revision plan.

Each conference profile defines its own agent panel, review scales, and evaluation criteria. HPDC uses a clarity reviewer; SC uses a systems reviewer. The profiles drive everything.

## Setup

```bash
cd backend && uv sync
cd frontend && npm install
```

## Configuration

ProtoNeo requires at least one LLM provider. Set up your `.env`:

```bash
cp .env.example .env
# Edit .env with your provider credentials
```

On first launch, open the Settings page to:
1. Connect providers (local LM Studio/Ollama, cloud API keys, or OAuth subscriptions)
2. Run model discovery
3. Select active models for each provider

No default models are configured. You choose what runs your reviews.

## Running

```bash
npm run kernel    # Backend on :5002
npm run frontend  # Frontend on :3000
```

## Testing

```bash
cd backend
uv run pytest tests/ -q   # 192 tests
```

## Structure

```
backend/
  protoneo/          # Kernel: agents, deliberation, knowledge graph, LLM routing
  applications/
    pc_panel/        # PC Panel: review orchestration, conference profiles, prompts
  tests/
frontend/
  src/               # Vue 3 + Vite + D3 knowledge graph visualization
```
