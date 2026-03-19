# ProtoNeo PC Panel: Pipeline Fix Plan

## Context for Next Session

This document is the handoff between Claude Code sessions. Read it fully before
doing any work. It contains the complete audit of the first end-to-end review run,
15 prioritized fixes, and a staged improvement roadmap.

### What is ProtoNeo PC Panel?

A multi-agent deliberation system for academic paper review. It parses a PDF,
builds a knowledge graph, then runs a panel of AI reviewers (independent reviews,
multi-round deliberation, meta-review, PC Chair synthesis). The goal is to
produce reviews comparable to human HPDC/SC conference reviewers for
pre-submission self-assessment.

### What happened in the prior session

1. **Removed all Google/Gemini providers** (banned from subscription). Cleaned
   the entire codebase: providers, catalogs, discovery, client routing, frontend,
   tests. 127 tests pass.

2. **Enforced local-only models for graph pipeline.** Subscription tokens
   (Anthropic, OpenAI) are reserved for review roles. Graph steps (ontology,
   extraction, coref, verification) only use local models. Enforced in both
   backend (`_resolve_graph_model`) and frontend (dropdown filtering).

3. **Built a preset system.** 6 built-in presets mapping models to pipeline
   steps and review roles. API endpoints (`GET /api/presets`,
   `POST /api/presets/{name}/activate`). Frontend preset selector on PanelHome.
   Presets reference specific homelab nodes (lan-dynamo, lan-mini) so they are
   site-specific and not committed to git.

4. **Ran first end-to-end review** on a real HPDC submission (Lapland paper).
   Used only local models (lan-dynamo with Qwen3.5 distilled reasoning, lan-mini
   with Qwen35-Distilled-i1-Q4_K_M). The run completed but revealed 15 bugs
   documented below.

### The real goal

We have 8 PDF reviews to submit to HPDC/SC by end of day. The pipeline must
produce correct, complete, high-quality reviews. Every fix below serves that
goal. We are not improving the pipeline for its own sake; we are improving it
so the next 8 runs produce reviews we can trust and submit.

### Homelab hardware

- **lan-dynamo** (LM Studio): 28 models downloaded, primary model is
  `qwen3.5-35b-a3b-claude-4.6-opus-reasoning-distilled-i1` (127.5 t/s, 262K context).
  Also has `qwen3.5-35b-a3b`, nemotron-3-nano-30b, granite-4-h, ministral variants.
- **lan-mini** (llama-server): `Qwen35-Distilled-i1-Q4_K_M` (113.2 t/s, 262K context),
  `Qwen3.5-35B-A3B-UD-Q4_K_XL`.
- **Cloud** (for reviews only): Anthropic Claude Max (opus-4-6, sonnet-4-6, haiku-4-5),
  OpenAI ChatGPT Pro (gpt-5.4, gpt-5.3-codex, etc.).

### Key architecture files

- `backend/protoneo/deliberation/patterns.py`: Parallel and round-robin execution
- `backend/protoneo/deliberation/engine.py`: Orchestrates phases
- `backend/protoneo/llm/client.py`: Multi-provider LLM client
- `backend/applications/pc_panel/pipeline.py`: 7-step graph pipeline + review stages
- `backend/applications/pc_panel/review.py`: Agent config builder, review parsers
- `backend/protoneo/knowledge/paper_graph.py`: Graph model, D3 export, utilization
- `backend/protoneo/knowledge/graph_extractor.py`: Section-aware graph extraction
- `backend/protoneo/knowledge/graph_verifier.py`: 3-check verification
- See `CLAUDE.md` for full architecture and import conventions.

---

## Staged Improvement Roadmap

The fixes below are tactical bug fixes. But the strategic path to trustworthy
reviews is incremental validation. Each stage adds one capability and we verify
it works before adding the next.

### Stage 1: Paper-only reviews (no graph, minimal pipeline)

Skip the graph pipeline entirely. Give agents the paper markdown and system
prompts. Verify that all 4 reviewers produce structured JSON reviews with
grounded strengths/weaknesses. This isolates review quality from graph quality.

**Test:** Run with `graph_only=False` but skip graph enrichment in the user
message. Check all 4 reviews are complete, valid JSON, >500 words, with
section-specific citations.

### Stage 2: Better paper input

Clean the PDF-to-markdown conversion. Strip line number pollution. Verify
section headings, figures, tables, and equations are preserved. The paper
text is the foundation; garbage in means garbage reviews.

**Test:** Compare raw `paper_markdown` against the actual PDF. Spot-check
5 sections for completeness. Verify no truncation.

### Stage 3: Paper + graph context

Re-enable the graph pipeline. Restructure the reviewer summary from a 23K-char
wall of text to a 3K-char review briefing. Verify that reviewers actually
reference graph concepts in their reviews (target: >40% entity utilization).

**Test:** Run with graph. Compare review quality (specificity, grounding) to
Stage 1. Measure graph utilization. The graph should make reviews more specific,
not just longer.

### Stage 4: Full reviewer panel

Ensure all configured reviewers complete successfully. Fix the silent exception
swallowing and empty output problems. Verify deliberation produces genuine
back-and-forth, not role-confused monologues.

**Test:** All 4 reviewers produce independent reviews. Deliberation rounds
show cross-references to specific prior points. No identity confusion.

### Stage 5: Better deliberation and synthesis

Fix role confusion. Add duplicate detection. Improve meta-review validation.
Ensure the PC Chair review synthesizes all perspectives into a coherent,
actionable assessment.

**Test:** Meta-review correctly reports actual scores (not hallucinated).
PC Chair review has all HotCRP fields. Deliberation voices are distinct.

### Stage 6: Cloud models for reviews

Switch review roles from local to cloud models (Claude Opus for technical/meta,
Sonnet for systems/clarity, GPT-5.4 for novelty/skeptic). Keep graph pipeline
on local models. Compare review quality against Stage 4 local-only baseline.

**Test:** Side-by-side comparison on the same paper. Cloud reviews should show
deeper technical insight, better grounding, and more actionable feedback.

### Convergence criteria

The pipeline is ready for production use when:
- All reviewers complete on every paper (0% failure rate across 8 papers)
- Reviews parse as valid JSON with all required fields
- Strengths/weaknesses cite specific sections, figures, or data
- Meta-review correctly aggregates individual scores
- Total pipeline time under 15 minutes per paper (local graph + cloud reviews)
- The human reviewer (you) reads the output and says "I would submit this"

---

## Audit Results: First End-to-End Run

**Session audited:** `2461463451f14c45a155bb32ee31b57f`
**Paper:** DDIO-aware Multi-objective Resource Partitioning (Lapland)
**Models:** lan-dynamo (qwen3.5 distilled reasoning), lan-mini (Qwen35-Distilled-i1-Q4_K_M)
**Pipeline:** 818s graph (ontology 40s, extract 450s, coref 211s, verify 116s) + 430s reviews = ~21 min total
**Graph:** 296 nodes (220 semantic, 76 structural), 723 edges (335 semantic, 388 structural), 91% connected
**Reviews:** 3 of 4 reviewers produced output (technical MISSING, skeptic EMPTY)
**Graph utilization by reviewers:** 14.2% (29/204 entities referenced)
**Date:** 2026-03-19

---

## Fix 1: Empty reviewer output (skeptic returned 0 chars)

**Severity:** CRITICAL
**Impact:** One of three independent reviewers produced nothing. Deliberation proceeded with a phantom reviewer who had no initial position.

**Root cause:** The streaming path in `base.py:148-160` accumulates chunks from `LLMClient.stream()`. The skeptic model (`lan-mini`) likely produced only `<think>...</think>` reasoning tokens with no final answer. `_strip_thinking()` stripped the thinking block, leaving empty content. Resource contention is a factor: both clarity and skeptic ran concurrently on `lan-mini` during the parallel phase. No error was raised because the model technically "succeeded" with empty output. `ParallelPattern` at `patterns.py:147-159` only records errors for actual exceptions, not for agents that succeed with empty content.

**Fix:**
- `backend/protoneo/deliberation/patterns.py`: After an agent completes, check if `content` is empty. If so, retry once with the same prompt. If still empty, emit `agent_error` event and exclude from subsequent phases.
- `backend/protoneo/llm/client.py`: After `_strip_thinking()`, if content is empty and the raw response had thinking tokens, log a WARNING with the thinking content length.
- Add an `empty_output_retries` counter to the session's step state for observability.
- Consider: stagger `lan-mini` agents (run clarity first, then skeptic) to avoid contention.

**Files:** `patterns.py`, `client.py`

---

## Fix 2: Technical reviewer missing from independent review

**Severity:** CRITICAL
**Impact:** The most important reviewer for HPDC never ran phase 1 but appeared in deliberation with fabricated opinions.

**Root cause:** `ParallelPattern.execute()` uses `asyncio.gather(*tasks, return_exceptions=True)`. When the technical agent raised an exception, it was logged to Python stderr via `logger.error()` at `patterns.py:150` and an `agent_error` event fired to the WebSocket bus, but neither was persisted to the session result. The output was silently dropped. The agent then appeared in deliberation because deliberation runs from the config's agent list, not from agents that actually completed phase 1.

We do NOT know the actual error. Context overflow is ruled out (~28K tokens vs 262K window, only 10% usage). Possible causes: LM Studio request timeout on lan-dynamo, concurrent model loading/contention (novelty also runs on dynamo), LiteLLM routing error, or transient network failure. Original logs were lost when backend was restarted.

**Fix:**
- `backend/protoneo/deliberation/patterns.py`: When `return_exceptions=True` catches an exception, persist the error to the phase result (add `failed_agents` list to the phase output). Retry the failed agent once before giving up.
- `backend/protoneo/deliberation/engine.py`: Before starting the deliberation phase, filter out agents that have no independent review output. They should not participate in deliberation without having reviewed the paper.
- Add a `phase_agents_expected` vs `phase_agents_completed` check with a bus event: `agent_missing`.
- Persist all agent errors to the session so they survive backend restarts.

**Files:** `patterns.py`, `engine.py`, `session.py` (add `phase_errors` field)

---

## Fix 3: Session metadata not persisted

**Severity:** CRITICAL
**Impact:** `conference: ?`, `filename: ?`, `paper_title: ?`, `paper_text: 0 chars`, `paper_markdown: 0 chars`, `config.agents: {}`. Post-review operations (PC Chair, export, refinement) have no paper context.

**Root cause:** The pipeline writes `session.paper_text = doc.text` and `session.paper_markdown = doc.markdown` only at step 7 (summarize), line 668 of `pipeline.py`. If metadata extraction finds the title earlier (step 2), it updates the session, but the agent configs and deliberation configs are never persisted to the session.

**Fix:**
- `backend/applications/pc_panel/pipeline.py`: Immediately after `_run_graph_pipeline` starts (before step 1), persist ALL session data:
  ```python
  session.paper_text = doc.text
  session.paper_markdown = doc.markdown or ""
  session.config["agents"] = {k: v.model_dump() for k, v in agent_configs.items()}
  session.config["deliberation"] = delib_config.model_dump()
  session.config["metadata"]["conference"] = profile.slug
  session.config["metadata"]["filename"] = doc.filename
  await _session_manager.update(session)
  ```
- Move the `paper_text`/`paper_markdown` persistence from step 7 to the very start of the pipeline.

**Files:** `pipeline.py`

---

## Fix 4: Graph D3 format loses entity type information

**Severity:** HIGH
**Impact:** All 296 nodes show `type: unknown`. Graph utilization checker cannot match entities to review content. Frontend graph visualization loses color-coding by type.

**Root cause:** `PaperGraph.to_d3_format()` maps nodes to D3 format but the `type` field is not extracted from the `labels` array. Nodes store type as `labels: ["Entity", "Concept"]` but D3 format expects a flat `type` string.

**Fix:**
- `backend/protoneo/knowledge/paper_graph.py` in `to_d3_format()`: Extract the semantic type from `labels`:
  ```python
  # labels = ["Entity", "Concept"] -> type = "Concept"
  # labels = ["Entity", "Section"] -> type = "Section"
  semantic_type = next((l for l in node.labels if l != "Entity"), "Entity")
  ```
- Also populate the `group` field for D3 force-graph coloring.

**Files:** `paper_graph.py`

---

## Fix 5: JSON fence stripping inconsistency

**Severity:** HIGH
**Impact:** Clarity reviewer's valid JSON wrapped in ` ```json ``` ` fails initial parse. Meta-review has the same problem. The `_extract_json()` utility handles this, but direct `json.loads()` calls in other places do not.

**Root cause:** Local models (especially Qwen35-Distilled) consistently wrap JSON output in markdown code fences. The `_extract_json()` in `review.py` handles this, but `parse_meta_review()` and other parsers do a bare `json.loads()` first and only fall back to fence-stripping on failure. The problem is that the raw content string (with fences) gets stored in `raw_content`, and some code paths check `is_json` before using the parsed data.

**Fix:**
- Create a shared `strip_json_fences(text: str) -> str` utility in `review.py` or a small `utils.py`.
- Apply it BEFORE `json.loads()` in every parser: `parse_review_output()`, `parse_meta_review()`, `_parse_final_review()`.
- The LLM client could also strip fences in `_strip_thinking()` as a belt-and-suspenders approach.

**Files:** `review.py`, `pipeline.py`

---

## Fix 6: Graph utilization matching is broken

**Severity:** HIGH
**Impact:** Only 14% graph utilization reported. Reviewers clearly discuss DDIO, LLC, Lapland, etc. but the matcher can't find them because graph node labels are 160-character sentences, not short identifiers.

**Root cause:** `compute_utilization()` in `paper_graph.py` does substring matching of node labels against review text. But node labels are verbose descriptions like "Lapland: First coordinated framework jointly partitioning CPU cores, LLC, DDIO ways, and memory bandwidth..." so a reviewer writing "Lapland" doesn't match.

**Fix:**
- `backend/protoneo/knowledge/paper_graph.py` in `compute_utilization()`: Match on BOTH the full label AND the `name` field (which is shorter). Also extract the first word/phrase before any colon or parenthesis as a short alias.
- Add a `short_name` extraction: `label.split(":")[0].strip()` or `label.split("(")[0].strip()`.
- Match case-insensitively.
- Consider matching individual key terms from the label (tokenize and check).

**Files:** `paper_graph.py`

---

## Fix 7: Graph summary not actionable for reviewers

**Severity:** HIGH
**Impact:** The 23K-char reviewer summary was injected but reviewers made 0 graph references in deliberation. The summary format is a raw dump of entities and relationships, not structured review guidance.

**Root cause:** `to_reviewer_summary()` in `paper_graph.py` produces a comprehensive but unstructured listing of all entities by type. Reviewers see a wall of text and ignore it. The summary needs to be a concise, actionable briefing that highlights review-relevant findings.

**Fix:**
- Restructure `to_reviewer_summary()` to produce 3 sections:
  1. **Key Claims** (5-10 bullets): What the paper claims, with section references
  2. **Methodology Concerns** (flagged by verification): Grounding issues, missing connections
  3. **Evaluation Summary**: Baselines, metrics, result claims with figure/table refs
- Cap at 3000 chars. Dense, scannable, with section numbers.
- Consider a separate `to_review_briefing()` method that's even shorter and pointed.

**Files:** `paper_graph.py`

---

## Fix 8: Deliberation role confusion

**Severity:** MEDIUM
**Impact:** Clarity and skeptic in Round 2 both claim to be "Technical Depth Reviewer". Models are confused about their identity.

**Root cause:** The deliberation prompt injects previous round outputs from all reviewers. The model reads "Technical Depth Reviewer" in the context and then adopts that identity. The system prompt says which role the agent plays, but the model's instruction-following breaks when it sees strong role labels in the user context.

**Fix:**
- `backend/protoneo/deliberation/session.py` or `patterns.py`: When injecting prior round outputs into the deliberation context, prefix each block with clear delimiters: `[OTHER REVIEWER - DO NOT ADOPT THIS IDENTITY]` or use a structured format that separates identity from content.
- Reinforce the agent's own identity at the end of the system prompt: `REMINDER: You are the {role}. Do not adopt any other reviewer's identity.`

**Files:** `patterns.py` or `session.py`, prompt templates

---

## Fix 9: Verification step added orphan nodes

**Severity:** MEDIUM
**Impact:** 4 orphan nodes (Result entities like "32% performance degradation", "110s stabilization time") were added by verification but have no edges connecting them to anything.

**Root cause:** The verification completeness pass identifies missing concepts and adds them as nodes, but doesn't always create edges linking them to the relevant section or parent concept.

**Fix:**
- `backend/protoneo/knowledge/graph_verifier.py`: After adding any new entity, ensure at least one edge connects it (e.g., `APPEARS_IN` to the section where the concept was found, or `SUPPORTS` to the relevant claim).
- Add a post-verification sweep that removes any nodes with zero edges.

**Files:** `graph_verifier.py`

---

## Fix 10: Coref produced 4 ALIAS_OF self-loops

**Severity:** LOW
**Impact:** 4 self-referential edges where a node aliases itself. Harmless but indicates a matching bug.

**Root cause:** The coreference resolver matched an entity to itself and created an ALIAS_OF edge.

**Fix:**
- `backend/protoneo/knowledge/coref_resolver.py`: Add a guard: `if source_uuid == target_uuid: continue`.

**Files:** `coref_resolver.py`

---

## Fix 11: Node labels too verbose for UI and matching

**Severity:** MEDIUM
**Impact:** Node labels are full sentences (100-160 chars). The D3 graph visualization is unreadable. Concept matching for utilization fails.

**Root cause:** The ontology generates verbose entity descriptions and the extraction step uses them as labels.

**Fix:**
- `backend/protoneo/knowledge/graph_extractor.py`: When creating nodes, use a concise `name` (under 50 chars) and store the full description in `attributes.description`.
- If the ontology returns verbose names, truncate to the first phrase or key term.
- Add a `display_name` field that's UI-friendly.

**Files:** `graph_extractor.py`, potentially `paper_ontology.py`

---

## Fix 12: Near-duplicate deliberation outputs (clarity = skeptic in round 2)

**Severity:** HIGH
**Impact:** Round 2 clarity and skeptic outputs differ by 9 characters. Both impersonate "Technical Depth Reviewer". Two of three deliberation voices collapsed into one. The panel lost diversity of perspective.

**Root cause:** Both agents run on `lan-mini` with the same base model. The deliberation prompt injects prior round text containing strong "Technical Depth Reviewer" identity markers. The small model latches onto the dominant voice. Combined with Fix 8 (role confusion), this means the deliberation is effectively one voice repeated three times.

**Fix:**
- Same as Fix 8 (identity reinforcement in prompts), but additionally:
- `backend/protoneo/deliberation/patterns.py`: After collecting round outputs, compare consecutive outputs. If cosine similarity > 0.95 (or simple char-level diff < 5%), log a WARNING and flag the session.
- Consider: use different models for clarity vs skeptic to force diversity. Or inject a diversity prompt: "Your perspective must differ from the other reviewers. Do not repeat their analysis."

**Files:** `patterns.py`, prompt templates

---

## Fix 13: Meta-review hallucinated score_distribution

**Severity:** MEDIUM
**Impact:** `score_distribution` contains `{"technical": 11, "novelty": 9, "clarity": 10, "skeptic": 3, "artifact": 7}` on a 1-5 scale, plus a phantom "artifact" reviewer that doesn't exist in this session.

**Root cause:** The meta-review model generated plausible-looking but incorrect data. `parse_meta_review()` at `review.py:293` copies `score_distribution` verbatim without validating scores are in range or reviewer names match the session.

**Fix:**
- `backend/applications/pc_panel/review.py` in `parse_meta_review()`: Validate `score_distribution` values are in the merit scale range (1-5). Strip any reviewer keys not in the session's agent list.
- Alternatively, compute `score_distribution` from the actual independent review outputs rather than trusting the meta-reviewer to report them.

**Files:** `review.py`

---

## Fix 14: Line number pollution in paper text

**Severity:** MEDIUM
**Impact:** First ~116 lines of `paper_text` and `paper_markdown` are pure line numbers from PDF parsing. Trailing line numbers also present. Wastes tokens in every agent call.

**Root cause:** Docling PDF parser extracts line numbers from the two-column ACM format as content.

**Fix:**
- `backend/protoneo/knowledge/parser.py` or `backend/protoneo/knowledge/metadata.py`: Add a post-parse cleanup step that strips leading/trailing runs of bare line numbers (regex: lines matching `^\d+$` at the start/end of the document).
- Be careful not to strip legitimate single-number content (e.g., table cells).

**Files:** `parser.py` or `metadata.py`

---

## Fix 15: SessionResponse API omits config and paper data

**Severity:** HIGH
**Impact:** `GET /api/sessions/{id}` returns only `{session_id, status, created_at, result, error}`. Conference, filename, agent configs, paper_text, paper_markdown are all lost in the API response despite being stored on disk.

**Root cause:** `SessionResponse` schema at `routes.py:201-206` was designed minimally. The full `Session` model has all the data, but the API endpoint serializes only a subset.

**Fix:**
- `backend/protoneo/api/routes.py`: Include `config`, `paper_text` (length, not full content), `paper_markdown` (length), `pipeline_steps`, `current_stage`, and `paper_graph` summary stats in the response.
- Or add a `GET /api/sessions/{id}/full` endpoint that returns the complete session.

**Files:** `routes.py`

---

## Execution Order

**Phase 1: Critical fixes (do first, re-run immediately)**
1. Fix 3 (session persistence) — 10 min
2. Fix 2 (missing technical reviewer, silent exception) — 30 min
3. Fix 1 (empty output retry, skeptic) — 20 min
4. Fix 5 (JSON fence stripping) — 10 min
5. Fix 15 (SessionResponse includes config) — 15 min
6. Fix 14 (line number pollution cleanup) — 10 min

**Phase 2: Review quality (do before next batch)**
7. Fix 8 + Fix 12 (deliberation identity + duplicate detection) — 30 min
8. Fix 4 (D3 type mapping) — 15 min
9. Fix 6 (utilization matching) — 20 min
10. Fix 7 (reviewer summary restructure) — 30 min
11. Fix 13 (meta-review score validation) — 10 min

**Phase 3: Graph polish (do after reviews submitted)**
12. Fix 9 (orphan nodes) — 10 min
13. Fix 10 (self-loop guard) — 5 min
14. Fix 11 (verbose labels) — 20 min

---

## Success Criteria for Next Run

- All 4 configured reviewers produce independent reviews (>500 words each)
- No empty outputs
- Session metadata fully populated (conference, filename, paper_title, paper_text)
- Agent configs persisted to session before pipeline starts
- Graph nodes have correct types in D3 format (18 types, not "unknown")
- Graph utilization > 40% (reviewers reference graph concepts)
- Meta-review and all individual reviews parse as valid JSON
- Reviewer summary under 3000 chars, structured as review briefing
- No orphan nodes in final graph
- No ALIAS_OF self-loops
- Deliberation: each reviewer maintains its own identity (no role confusion)

## Confirmed Graph Quality (for reference)

The graph pipeline itself works well. 18 entity types captured:
Concept (66), WorkloadType (35), Reference (28), Section (27), Metric (27),
Method (27), Result (16), Diagram (12), PartitioningStrategy (11), Baseline (11),
Dataset (9), ReinforcementLearningComponent (7), DDIOSharingMode (5), Table (4),
Claim (4), Equation (4), InterferencePattern (2), Paper (1).

17 edge types including: APPEARS_IN (325), USES (71), PART_OF (52),
EVALUATES_ON (42), CITES (37), HAS_SECTION (34), CONTAINS (29),
ACHIEVES_IMPROVEMENT_OVER (19), ACHIEVES (18), COMPARED_AGAINST (16), ALIAS_OF (16).

All 9 key paper concepts found in graph: DDIO (53x), LLC (24x), LinUCB (9x),
Lapland (33x), contextual bandit (3x), colocation (40x), throughput (30x),
fairness (25x), workload (39x). The graph content is excellent. The problem
is surfacing it to reviewers effectively.

---

## How to Use This Document

Start a new Claude Code session. Say: "Read PIPELINE-FIXES.md and execute
Stage 1 of the roadmap." Claude will read this file, understand the full
context, and begin implementing fixes in the right order.

For targeted work, say: "Read PIPELINE-FIXES.md and implement Fix 2
(missing technical reviewer)."

After each stage, run a test review on the Lapland paper and compare against
the audit results above. The session data is at:
`backend/data/sessions/2461463451f14c45a155bb32ee31b57f.json`
