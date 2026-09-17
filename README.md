<p align="center">
  <strong>PROTONEO</strong><br>
  <em>A composable deliberation engine. First application: academic paper review.</em>
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-0.2.0-blue" />
  <img alt="Python" src="https://img.shields.io/badge/python-3.12-3776ab" />
  <img alt="License" src="https://img.shields.io/badge/license-AGPL--3.0-green" />
  <img alt="Status" src="https://img.shields.io/badge/status-beta-yellow" />
</p>

ProtoNeo coordinates specialized agents through structured, source-grounded deliberation. Its kernel manages model routing, ordered phases, bounded concurrency, validated outputs, persistent sessions, and recovery. Applications supply their own roles, prompts, knowledge domain, and output contracts.

The first application turns an academic manuscript into a review packet: independent assessments, optional discussion rounds, and a meta-review with venue-specific scores, evidence, strengths, weaknesses, and revision suggestions. A Vue dashboard exposes the manuscript, knowledge graph, live discussion, editable final review, and exports.

**Release status:** beta. Version 0.2.0 was validated on Linux with live local and LAN models (LM Studio and llama.cpp servers), real Docling parsing, complete manuscript-only and graph-backed reviews, and a clean install of the built wheel. Cloud providers were not validated for this release; see [Validation](#validation-and-packaging). Model-generated feedback can contain confident errors and needs human judgment before it informs a submission or conference decision.

## What is included

- **Reusable deliberation kernel:** parallel, sequential, and round-robin phases; configurable concurrency, timeouts, and validation retries; streaming usage accounting; application-owned validation policies.
- **Recoverable execution:** atomic session persistence, checkpoint reuse when inputs and configuration match, cancellation cleanup, and ordered recovery of interrupted discussions.
- **Configurable review panel:** enable or disable reviewers, select exact models per role, choose a preset, and control the number of discussion rounds.
- **Two review workflows:** manuscript plus knowledge graph, or manuscript only to reduce model calls. Graph preparation can also run independently, and saved graphs can be imported for review.
- **Grounded extraction:** Docling layout and table extraction, optional OCR and formula decoding, and optional figure descriptions through a configured vision endpoint.
- **Review tools:** sequential batch processing, graph inspection and editing, PC-chair assistance, an evidence-preserving review editor, and Markdown, PDF, and supported offline-form exports.
- **Venue profiles:** a built-in Adaptive Venue profile and importable CFPs, review forms, Markdown, YAML, or JSON templates.

## Quick start

### Requirements

| Tool | Supported version / purpose |
| --- | --- |
| Python | **3.12.x** (`>=3.12,<3.13`) |
| Node.js | **20.19+ within Node 20, or 22.12+**; release builds used Node 22 LTS |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | Python environment and package management |
| Model service | A configured local/LAN endpoint or supported cloud provider |

Node is needed to develop or build the frontend. A wheel built using the packaging steps below includes the frontend and does not need Node at runtime. Docling may download extraction models on first use. PDF export uses WeasyPrint; consult its [installation requirements](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation) if your system lacks its native libraries. Linux is the validated environment for this candidate; macOS and Windows have not been release-validated.

### Install and run

```bash
git clone https://github.com/akougkas/protoneo.git
cd protoneo
uv sync --locked
npm --prefix ui ci
npm --prefix ui run build
uv run protoneo --host 127.0.0.1 --port 5002
```

Open **http://localhost:5002**. The backend serves the built frontend and the API from the same address. `uv run python run.py` uses the same CLI. The command above binds to loopback; the CLI default without `--host` is `0.0.0.0`.

For frontend development, run these in separate terminals from the repository root:

```bash
# Terminal 1: backend with reload
uv run protoneo --host 127.0.0.1 --port 5002 --reload

# Terminal 2: Vite with HTTP and WebSocket API proxying
npm --prefix ui run dev -- --host 127.0.0.1
```

Open **http://localhost:3000** for development. Vite proxies `/api` to backend port 5002; update `ui/vite.config.js` if you change that port.

### Connect models

1. Open **Settings** and enable or add the provider you intend to use.
2. For a local server, start the server and load a model. Fresh settings include disabled LM Studio (`http://localhost:1234/v1`) and Ollama (`http://localhost:11434`) entries. Enable the appropriate entry; LAN services can be added with their own URLs.
3. Click **Refresh All** to discover models, then select the active models you want available to the panel.
4. On **Home**, select a preset or assign models to individual roles. Keep at least one reviewer and a meta-reviewer enabled; graph workflows also need graph-step model assignments.

For cloud providers, configure the supported connection in Settings or set the relevant `OPENAI_API_KEY` or `OPENROUTER_API_KEY` in your shell or a local `.env`. See [.env.example](.env.example) for additional configuration. Shell environment values take precedence over `.env`.

The setup indicator checks assignments, enabled providers, and configured credentials. **“Setup ready” does not ping a provider or run a model.** It cannot establish that a server is reachable, that a key is still valid, or that a selected model can produce valid review output. Explicit model choices are preserved; unknown custom model names are sent to the configured endpoint without fuzzy substitution. Some local servers, including LM Studio, answer an unknown model name with whichever model is loaded. ProtoNeo rejects such responses from local and LAN endpoints with an error naming the served model, so reviews are never attributed to a model that did not produce them.

Graph extraction and figure description run with reasoning disabled. LM Studio ignores the usual chat-template switch, so ProtoNeo sends `reasoning_effort: "none"` to endpoints discovered as LM Studio. For other servers, prefer non-reasoning models for graph steps; a reasoning model can spend thousands of tokens per extraction call.

### Run your first review

1. Select **Adaptive Venue**, or import and select a venue template. A venue is required.
2. Upload a PDF and choose the panel. The default upload limit is 100 MB per file.
3. Choose **Manuscript + knowledge graph** or **Manuscript only**, and set the discussion rounds. **Zero rounds** runs independent reviews followed by synthesis; it does not add a discussion round. The UI offers 0–4 rounds, and the API accepts 0–20.
4. Optionally enable **Fast PDF parsing** to skip figure descriptions, or **Inspect graph before review** to pause after graph preparation. Otherwise, a full review proceeds automatically.
5. Run **Preflight Check**, inspect the extracted metadata and any blockers, then start the review when the setup is ready.
6. Follow the session, inspect the evidence, edit the final review, and export the packet.

The displayed review-turn count includes initial reviewer outputs, discussion turns, and the meta-review. Graph extraction and retries add model calls. Disabling a reviewer removes that reviewer from execution.

## PDF extraction and figure descriptions

Docling extracts layout, manuscript text, and tables. In **Settings → PDF extraction**, enable OCR for scanned manuscripts and formula decoding when needed. Both are disabled by default and increase parsing work. An empty text extraction fails with an actionable error instead of starting a review without source material.

For figure descriptions, configure an OpenAI-compatible vision service under **Settings → VLM Figure Description**. Enter its full chat-completions URL, such as `http://localhost:8081/v1/chat/completions`, and the exact model ID served by that endpoint. Set the URL and model before enabling descriptions, then use **Test Connection**. The test asks the model to describe a small generated chart and reports its answer, so it confirms image input and the served model rather than just reachability.

**Fast PDF parsing only skips VLM figure descriptions.** It retains layout and text extraction and respects your OCR and formula settings. Preflight also skips VLM descriptions; a reachable vision service alone does not make a parsed manuscript vision-grounded.

Model inputs go to the providers you select. To keep review inference on your machine, use local assignments for every reviewer and graph step, keep any vision endpoint local, and disable external review search with `PROTONEO_REVIEW_WEB_SEARCH=off`. Cloud/LAN assignments and enabled search tools can send manuscript content, figure crops, or derived queries to their configured services. Automatic graph routing prefers local models, while explicit cloud assignments are honored.

## Venue profiles, editing, and exports

The built-in **Adaptive Venue** profile supports general reviews. Use **Import Venue Template** on Home to create a profile from a CFP, review form, author instructions, or a compatible YAML/JSON profile. Inspect imported criteria and score labels before using them for a conference.

Imported profiles live under `~/.protoneo/paper_review/profiles/` by default, outside the repository. Custom configuration and profile directories are supported below. Private venue packs and manuscript packets are not bundled with the application.

Review output follows the selected venue's score scale. The editor validates score ranges, keeps labels aligned with numeric scores, preserves structured evidence during edits, and reports save errors. PC-chair assistance can propose refinements; decision fields are protected unless a decision change is explicitly requested.

Missing ratings remain unassessed. ProtoNeo does not invent confidence, expertise, nomination, or review-form dimensions to fill an export. Supported offline forms, including Linklings templates, require their mandatory ratings and identify missing fields when export cannot proceed. Check those fields in the final-review editor before exporting. Markdown and PDF packets preserve venue-specific scales and provenance.

## Recovery and batch workflows

Sessions retain source material, model choices, workflow options, graph checkpoints, accepted outputs, and usage. Retry can reuse compatible work; rebuilding a graph invalidates downstream review results. A resumed round-robin discussion reuses only the accepted prefix of an interrupted round, so later saved turns cannot leak into an earlier reviewer's context.

Pause, resume, step, and cancellation controls apply to background execution. Reopening a session restores its persisted state and reconnects the live event stream. Interrupted work can be retried; recovery does not silently declare an unfinished review complete.

Use batch review to process PDFs sequentially with the same panel and workflow settings. Batch upload validation finishes before sessions are created, and processing reports per-paper progress and failures. Graph-only preparation and imported-graph review are also available. The application API includes a packet-folder workflow for saved graphs and offline review templates.

## Configuration and storage

Settings persist across restarts in `~/.protoneo/settings.json`. The Settings UI manages provider endpoints, active models, inference options, discovery and benchmarks, PDF extraction, and the vision endpoint. Validation errors are shown in the UI; partial updates preserve unrelated configuration.

Set environment overrides before starting the server:

| Variable | Purpose / default |
| --- | --- |
| `PROTONEO_CONFIG_DIR` | Settings, OAuth token storage, and user profiles; defaults to `~/.protoneo` |
| `PROTONEO_DATA_DIR` | Parent of the session directory; defaults to checkout `data/`, or `~/.protoneo/data` in an installed package |
| `PROTONEO_SESSION_DIR` | Direct session-directory override; takes precedence over `PROTONEO_DATA_DIR` |
| `PROTONEO_PAPER_REVIEW_PROFILE_DIR` | Overrides the user venue-profile directory |
| `PROTONEO_UI_DIR` | Overrides the built frontend directory |
| `PROTONEO_HOST` / `PROTONEO_PORT` | Bind address and port; defaults to `0.0.0.0` / `5002` |
| `PROTONEO_MAX_UPLOAD_MB` | Maximum PDF upload size per file; defaults to `100` |
| `PROTONEO_APPS` | Comma-separated `module:attribute` manifests; overrides entry-point discovery |
| `PROTONEO_REVIEW_WEB_SEARCH` | Review search policy: `auto` by default; `off` disables it |

Uploads are stored under the configured session directory. Changing `PROTONEO_CONFIG_DIR` alone does not relocate session data; set `PROTONEO_DATA_DIR` or `PROTONEO_SESSION_DIR` as well when isolating an installation. The CLI reads `.env` from the current directory, falling back to the project root, without overriding existing environment values.

Optional search integrations include Brave (`BRAVE_API_KEY`), SearXNG (`SEARXNG_URL`), and Semantic Scholar (`SEMANTIC_SCHOLAR_API_KEY`). DuckDuckGo fallback is opt-in through `PROTONEO_ENABLE_DUCKDUCKGO_SEARCH=1`.

## Engine and application boundaries

| Path | Responsibility |
| --- | --- |
| [protoneo/deliberation/](protoneo/deliberation/) | Phase execution, persistence, recovery, and generic policy contracts |
| [protoneo/llm/](protoneo/llm/) | Provider connections, capability registry, routing, inference, and usage |
| [protoneo/knowledge/](protoneo/knowledge/) | Document parsing and configurable knowledge-graph processing |
| [protoneo/api/](protoneo/api/) | FastAPI lifecycle, task ownership, events, and application mounting |
| [apps/paper_review/](apps/paper_review/) | Review roles, venue contracts, readiness checks, workflows, and exports |
| [ui/](ui/) | Vue 3 application and Vite build |

The kernel does not import applications. Applications register manifests through the `protoneo.apps` package entry-point group; the included package registers Paper Review automatically. Use `--app module:attribute` or `PROTONEO_APPS` to select manifests explicitly. The generic `DeliberationPolicy` contract lets applications validate and repair their own outputs without putting paper-review rules in the engine.

Runtime configuration exposes `max_concurrency` (default 4), `max_attempts` (default 2), and per-phase `timeout_seconds` (default 600). Source and output budgets are checked against registered model context limits; oversized input produces an error rather than silently truncating the manuscript. Tune these values and model output budgets to the selected providers and available hardware.

Additional references: [kernel API](docs/kernel.md), [building applications](docs/building-apps.md), and [paper-review configuration](docs/paper-review.md).

## Validation and packaging

From a source checkout with dependencies installed:

```bash
uv run pytest -q
uvx ruff check protoneo apps --select F821,F822,F823
uv run python -m compileall -q protoneo apps
git diff --check
npm --prefix ui run build
uv build
```

Build the frontend **before** `uv build`. The wheel includes it as `protoneo/static`; the source distribution carries the built assets so a wheel built from the source distribution also serves the UI. Version 0.1.0 produces `dist/protoneo-0.1.0-py3-none-any.whl` and `dist/protoneo-0.1.0.tar.gz`.

The current 32-test suite uses controlled model responses. Additional candidate checks exercised browser setup and editing, malformed uploads and settings, batch execution, cancellation and resume ordering, real PDF table extraction, CLI reload, and serving the packaged UI outside the checkout. These checks establish specific runtime behavior, not model quality. Before publishing, validate configured providers with real manuscript reviews, inspect scores and evidence, and install the final artifacts in a clean environment.

## License

[AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html)
