<p align="center">
  <strong>PROTONEO</strong><br>
  <em>AI peer review for your papers, before you submit.</em>
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-0.1.0-blue" />
  <img alt="Python" src="https://img.shields.io/badge/python-%E2%89%A53.12-3776ab" />
  <img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green" />
  <img alt="Tests" src="https://img.shields.io/badge/tests-245%20passing-brightgreen" />
  <img alt="Platform" src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey" />
</p>

---

ProtoNeo is an AI review panel that reads your academic paper, builds a knowledge graph of its claims, and runs multiple AI reviewers through structured deliberation to produce a full review packet with scores, strengths, weaknesses, and revision suggestions.

Think of it as a practice defense for your paper. Upload a PDF, pick a venue, and get back the kind of feedback a program committee would give you.

## What You Get

- **Multi-agent review panel** with role-specialized reviewers (technical depth, novelty, clarity, skeptic)
- **Knowledge graph** extracted from your paper for grounded, citation-aware feedback
- **Real-time dashboard** showing agent deliberation, graph construction, and review progress
- **Figure analysis** via local Vision-Language Models (your figures never leave your machine)
- **Export** to Markdown or PDF review packets
- **Batch mode** for reviewing multiple papers in sequence
- **Conference profiles** with venue-specific criteria (ships with HPDC '26 and SC '26)

## Quick Start

### Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| **Python** | >= 3.12 | [python.org](https://www.python.org/downloads/) |
| **Node.js** | >= 18 | [nodejs.org](https://nodejs.org/) |
| **uv** | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **LLM service** | any | See [Choose Your LLM](#choose-your-llm) below |

### Install

```bash
git clone https://github.com/your-org/protoneo.git
cd protoneo

# Backend
uv sync

# Frontend
cd ui && npm install && cd ..

# Environment (optional, for cloud API keys)
cp .env.example .env
```

### Run

**Option A: Two terminals (development)**

```bash
# Terminal 1: backend
uv run python run.py

# Terminal 2: frontend dev server
cd ui && npx vite
```

Open **http://localhost:3000**

**Option B: Single process (production)**

```bash
cd ui && npx vite build && cd ..
uv run python run.py
```

Open **http://localhost:5002**

The backend serves the built frontend automatically.

### First Review

1. Open the app in your browser
2. Go to **Settings** and connect at least one LLM provider
3. Click **Refresh All** to discover available models
4. Select an active model for your provider
5. Go back to **Home**, select a venue, upload a PDF, and launch

---

## Choose Your LLM

ProtoNeo works with any OpenAI-compatible LLM endpoint. Pick what fits your setup.

### Local (recommended for privacy)

Your papers never leave your machine.

| Service | Default Port | Setup |
|---------|-------------|-------|
| [LM Studio](https://lmstudio.ai/) | `localhost:1234` | Download, load a model, start the server |
| [Ollama](https://ollama.ai/) | `localhost:11434` | `ollama run qwen3.5:35b-a3b` |

ProtoNeo auto-detects both on startup.

### Cloud

For stronger models or when you don't have a GPU.

| Provider | What You Need |
|----------|--------------|
| [OpenAI](https://platform.openai.com/) | Set `OPENAI_API_KEY` in `.env` |
| [OpenRouter](https://openrouter.ai/) | Set `OPENROUTER_API_KEY` in `.env` |

### LAN / Homelab

Running LLMs on another machine on your network? Add LAN endpoints in **Settings > AI Providers**.

---

## Figure Descriptions (Optional)

ProtoNeo can describe the figures in your paper using a local Vision-Language Model. This is optional but makes reviews significantly better because reviewers can reason about your charts, diagrams, and plots.

### Setup

1. Install [llama.cpp](https://github.com/ggerganov/llama.cpp) and build `llama-server`
2. Download a vision model (we recommend Qwen3-VL):

| Model | VRAM | Speed | Quality |
|-------|------|-------|---------|
| Qwen3-VL-8B | ~9 GB | 86 tok/s | Good |
| **Qwen3-VL-30B-A3B** | ~23 GB | 145 tok/s | **Best** |

3. Start the VLM server:

```bash
llama-server \
  --host 0.0.0.0 --port 8081 \
  --model Qwen3-VL-30B-A3B-Thinking-UD-Q5_K_XL.gguf \
  --mmproj Qwen3-VL-30B-mmproj-F16.gguf \
  --n-gpu-layers 99 --ctx-size 16384 \
  --cache-type-k q8_0 --cache-type-v q8_0 \
  --threads 8 --jinja
```

4. In ProtoNeo **Settings > VLM Figure Description**, enter:
   - **Endpoint URL:** `http://localhost:8081/v1/chat/completions`
   - **Model name:** `qwen3-vl-30b`
5. Click **Test Connection** to verify

When configured, every PDF upload automatically gets figure descriptions during parsing. You can skip this per-upload with the "fast parse" toggle.

---

## Platform Notes

### Linux

Works out of the box. For VLM support, ensure your GPU drivers support Vulkan or CUDA.

### macOS

Works out of the box. For VLM support with Apple Silicon, build llama.cpp with Metal:

```bash
cmake -B build -DGGML_METAL=ON && cmake --build build
```

### Windows

Use WSL2 for the best experience:

```bash
wsl --install
# Then follow the Linux instructions inside WSL
```

Native Windows works too, but `uv` and `llama.cpp` are easier under WSL.

---

## Configuration

### Settings UI

Everything is configurable from the browser. Go to **Settings** to manage:

- LLM providers and model selection
- VLM endpoint for figure descriptions
- Model scoring and benchmarking

Settings are saved to `~/.protoneo/settings.json` and persist across restarts.

### Environment Variables

For API keys and service URLs, edit `.env`:

```bash
# Cloud providers (uncomment and fill in what you use)
OPENAI_API_KEY=sk-...
OPENROUTER_API_KEY=sk-or-v1-...

# Optional tools for richer reviews
SEMANTIC_SCHOLAR_API_KEY=...
```

### Model Presets

ProtoNeo ships with presets that assign models to reviewer roles. Select a preset from the Home screen or create your own in Settings.

---

## How It Works

```
Upload PDF
    |
    v
[ Docling Parser ] -----> Layout analysis, table extraction, figure cropping
    |                      Optional VLM figure descriptions
    v
[ Knowledge Graph ] ----> Ontology, entities, relations, coreference, verification
    |                      6-step pipeline with checkpoint resume
    v
[ Review Panel ] -------> Technical, novelty, clarity, skeptic reviewers
    |                      Multi-round structured deliberation
    v
[ Meta-Reviewer ] ------> Synthesizes individual reviews into final packet
    |
    v
Review Packet (Markdown / PDF)
```

PDF parsing uses [Docling](https://github.com/DS4SD/docling) (IBM, MIT license) for layout-aware extraction. The knowledge graph grounds reviewer feedback in specific claims from your paper. Reviewers deliberate across multiple rounds, challenging and refining each other's assessments.

---

## Development

```bash
uv run pytest tests/ -q          # 245 tests, no network required
cd ui && npx vite build          # Production frontend build
```

See [docs/](docs/) for developer documentation:

- [Kernel API reference](docs/kernel.md)
- [Building applications on ProtoNeo](docs/building-apps.md)
- [Paper Review configuration](docs/paper-review.md)

---

## License

AGPL-3.0
