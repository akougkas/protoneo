# Next Session: LLM Council Prompt Optimization

## Context

Branch: `v0.1.0-restructure`. Commit `b7c16c6` completed the VLM pipeline, markdown cleanup, and graph prompt hardening. 245 tests pass. Frontend builds clean. The pre-reviewer pipeline (PDF parsing through graph generation) is finalized.

This session focuses on the reviewer and meta-reviewer prompts. A full audit of every LLM-facing string has been completed. The findings and improvement plan are below.

## Architecture Constraint

The kernel (`protoneo/`) owns agents, deliberation, tools, and LLM routing. The application (`apps/paper_review/`) owns prompts, personas, scoring schemas, conference profiles, and review orchestration. Never import from `apps/*` inside `protoneo/`. All prompt edits happen in `apps/paper_review/`.

## Audit Findings

### What's Already Strong

The prompt architecture is layered and well-structured:
1. **Shared prompt** (shared.md) sets venue calibration, grounding rules, dual-anonymous constraints, calibration scales, deliberation behavior, and output contract
2. **Role overlays** (technical.md, novelty.md, etc.) add persona-specific priorities
3. **Epistemic anchor** (prompts.py) injects focus-area directive
4. **Conference context** (profile YAML) provides venue scope and review form

The HPDC shared.md is exceptionally detailed (140 lines) with score calibration guidance, deliberation behavior rules, and concrete anti-patterns. SC shared.md is comparable. Both venues have complete prompt packs with manifests.

### Issues to Fix (Ordered by Impact)

#### 1. TOOLS ARE DEFINED BUT NEVER WIRED TO REVIEWERS

`protoneo/tools/semantic_scholar.py` and `protoneo/tools/web_search.py` exist and implement the `Tool` protocol. The shared prompts say "Work only from provided materials unless explicit retrieval is enabled." But there is no mechanism to enable retrieval. Reviewers cannot:
- Query Semantic Scholar for missing baselines or related work
- Search for recent papers that the submission should cite
- Verify whether claimed state-of-the-art is actually current

**Action**: Wire tool access through the deliberation engine. The tool registry exists (`protoneo/tools/types.py`, `ToolRegistry`). Add a tool-use phase or make tools available during deliberation rounds. This is a kernel-level change (engine must support tool dispatch) plus an application-level change (reviewer prompts must describe available tools and when to use them). Start with Semantic Scholar for citation checks.

#### 2. GRAPH IS SUMMARIZED BUT NOT QUERYABLE

The knowledge graph is passed to reviewers as a text summary (`graph_summary` field, max 5200 chars per `domain/config.yaml`). Reviewers cannot traverse the graph to:
- Check whether a claimed method actually connects to its evaluation datasets
- Verify that all baselines have COMPARED_AGAINST edges
- Find claims without supporting Result entities
- Detect disconnected subgraphs indicating under-evidenced sections

**Action**: Expose graph query capabilities to reviewers. Options:
- (a) Expand the graph summary to include structured analysis (missing edges, unsupported claims, entity type distribution)
- (b) Give reviewers a `query_graph` tool that can answer questions like "which claims lack Result edges?" or "what baselines are compared against Method X?"
- Option (a) is simpler and keeps the interaction single-turn. Add a `_build_review_graph_analysis()` function in `apps/paper_review/pipeline.py` that generates structured graph insights reviewers can reference.

#### 3. PC CHAIR PROMPT IS INLINE AND UNDER-SPECIFIED

`apps/paper_review/pipeline.py` lines 250-280 build the PC Chair prompt inline as a Python f-string. Unlike the reviewer prompts (which are multi-layered .md files with extensive guidance), the PC Chair gets a bare instruction to "produce the unified final review as a JSON object matching the HotCRP review form."

Missing from the PC Chair prompt:
- No calibration guidance (what score distributions look like for real PC chairs)
- No instruction to verify reviewer claims against the manuscript
- No guidance on how to weigh reviewer expertise levels
- No instruction to identify scoring inconsistencies (a reviewer listing 5 major weaknesses but scoring 3)
- No venue-specific context injection (the meta.md has this but the PC Chair bypasses it)
- The output schema uses different field names than the reviewer schema (`paper_summary` vs `summary`, `comments_for_pc` vs `internal_committee_concerns`)

**Action**: Move the PC Chair prompt to a proper .md file (`prompts/{venue}/pc_chair.md`). Align its output schema with the existing ReviewPacket structure. Add calibration and verification guidance matching the meta-reviewer standard.

#### 4. DELIBERATION PROMPTS LACK GRAPH-GROUNDED CROSS-REFERENCING

During deliberation (round-robin phase), reviewers see each other's outputs but are not explicitly instructed to:
- Cross-reference their peers' claims against the knowledge graph
- Check whether a concern raised by Reviewer A is supported or contradicted by graph entities
- Use section-level entity density to identify under-reviewed parts of the paper

The shared.md deliberation section says "go back to the manuscript" but doesn't mention the graph.

**Action**: Add a deliberation-specific instruction block in shared.md that tells reviewers to use the graph summary as a factual anchor during disagreements. When two reviewers disagree about whether evidence exists, the graph should be the tiebreaker.

#### 5. SCORING CALIBRATION NEEDS ANCHORING

The shared.md calibration section gives excellent descriptions for each score (1-5) and even provides a target distribution for 8 papers. But during single-paper review, reviewers tend to anchor on 3 (borderline) because they lack reference papers.

**Action**: Add comparative anchoring language. Instead of just "Use 4 when the paper is solid," add: "A score of 4 means you would actively argue FOR acceptance at a PC meeting. A score of 2 means you would actively argue AGAINST. A score of 3 means you see the outcome as genuinely uncertain and dependent on what your co-reviewers find." This shifts the framing from absolute quality to committee behavior.

#### 6. FIGURE DESCRIPTIONS NOT LEVERAGED BY REVIEWERS

The VLM now produces rich figure descriptions inline in the markdown (chart types, axes, data series, trends, quantitative observations). But the reviewer prompts don't mention figures or the VLM descriptions. Reviewers may not realize that the "prose paragraphs" between section headers are machine-generated figure analyses they should verify and cite.

**Action**: Add a note in shared.md under "Available context" explaining that figure descriptions are VLM-generated and inline. Tell reviewers to: (a) verify figure descriptions match the paper's claims, (b) cite specific figure analysis when discussing experimental results, (c) flag any figure descriptions that seem inaccurate or incomplete.

#### 7. META-REVIEWER SCHEMA DIVERGES FROM PC CHAIR SCHEMA

The meta-reviewer (meta.md) output has `final_recommendation`, `submission_readiness`, `prioritized_revision_plan`. The PC Chair (pipeline.py inline) has `overall_merit`, `submission_readiness`, `revision_actions`. The field names and structures differ. The `ReviewPacket` has both `meta_review` and `pc_chair_review` as separate fields, which creates redundancy.

**Action**: Decide whether the PC Chair review is a refinement of the meta-review or a replacement. If refinement, align the schemas. If replacement, make the meta-review produce the final packet and remove the PC Chair step. The current flow runs meta-review and then PC Chair, which is redundant (two synthesis passes over the same data). Recommendation: merge them. The meta-reviewer IS the PC Chair. One synthesis, one output.

#### 8. TEMPERATURE SETTINGS ARE REASONABLE BUT COULD BE TIGHTER

Current settings from `review.py`:
- technical: 0.2 / top_p 0.9
- systems: 0.2 / top_p 0.9
- skeptic: 0.15 / top_p 0.85
- artifact: 0.15 / top_p 0.85
- novelty: 0.3 / top_p 0.9
- clarity: 0.3 / top_p 0.9
- meta: 0.4 / top_p 0.9

The meta-reviewer at 0.4 is higher than reviewers, which makes sense for synthesis but risks creative interpretation. Consider dropping to 0.3.

The novelty/clarity reviewers at 0.3 produce more varied language which is fine for those roles.

No changes strictly needed, but document the reasoning for each setting.

#### 9. MINOR PROMPT ISSUES

- SC meta.md (32 lines) is much thinner than HPDC meta.md (97 lines). The SC version lacks the output schema, scoring consistency checks, and "do not invent scores" guardrail. Copy the relevant structure from HPDC.
- SC role overlays (technical: 37 lines, systems: 36 lines) are thinner than HPDC equivalents (technical: 66 lines). They work but could benefit from SC-specific guidance (scaling requirements, paper type awareness).
- The `no_chain_of_thought: true` guardrail in prompt-pack.yaml is metadata that isn't enforced anywhere. The shared.md already says "Do not output chain-of-thought." Consider removing the guardrail field or enforcing it in the agent base.
- `domain/config.yaml` has `verify_system_prompt` that overrides the kernel's default, but the prompt text is the old version without `<think>` tag support. Update it.

## Execution Plan

### Phase 1: Graph Analysis for Reviewers (app-level)
- Build `_build_review_graph_analysis()` in `apps/paper_review/pipeline.py`
- Generate structured insights: claim coverage, baseline completeness, unsupported claims, entity distribution by section
- Inject into reviewer user messages alongside the existing summary

### Phase 2: Prompt Improvements (app-level, `apps/paper_review/prompts/`)
- Add figure description awareness to shared.md
- Add graph-grounded deliberation instructions to shared.md
- Strengthen scoring calibration anchoring
- Expand SC meta.md to match HPDC meta.md structure
- Expand SC role overlays with venue-specific depth
- Create `pc_chair.md` or merge PC Chair into meta-reviewer

### Phase 3: PC Chair Consolidation (app-level)
- Move inline PC Chair prompt from `pipeline.py` to `prompts/{venue}/pc_chair.md`
- Align output schema with ReviewPacket
- Or merge with meta-reviewer (preferred)

### Phase 4: Tool Wiring (kernel + app)
- Add tool dispatch support to deliberation engine (kernel)
- Wire Semantic Scholar tool to reviewer agents
- Add tool-use instructions to reviewer prompts
- Start with citation verification during deliberation rounds

### Phase 5: Testing
- Run full pipeline on 2-3 papers from `.archived/reviews-pending/`
- Compare review quality before/after changes
- Verify JSON output parsing still works with any prompt changes
- Ensure 245 tests still pass after every change

## Files to Edit

| Phase | File | What |
|-------|------|------|
| 1 | `apps/paper_review/pipeline.py` | Add `_build_review_graph_analysis()` |
| 1 | `apps/paper_review/review.py` | Inject graph analysis into user message |
| 2 | `apps/paper_review/prompts/hpdc26/shared.md` | Figure awareness, graph deliberation, calibration |
| 2 | `apps/paper_review/prompts/sc26/shared.md` | Same changes |
| 2 | `apps/paper_review/prompts/sc26/meta.md` | Expand to match HPDC depth |
| 2 | `apps/paper_review/prompts/sc26/technical.md` | Expand with SC-specific depth |
| 2 | `apps/paper_review/prompts/sc26/systems.md` | Expand with SC-specific depth |
| 3 | `apps/paper_review/pipeline.py` | Extract PC Chair prompt, consolidate with meta |
| 3 | `apps/paper_review/prompts/{venue}/pc_chair.md` | New file or merge into meta.md |
| 4 | `protoneo/deliberation/engine.py` | Add tool dispatch in round-robin phase |
| 4 | `protoneo/agents/base.py` | Add tool-use capability to BaseAgent |
| 4 | `apps/paper_review/prompts/*/shared.md` | Add tool-use instructions |
| 5 | `apps/paper_review/domain/config.yaml` | Update verify_system_prompt |

## Key Constraints

- Surgical edits only. Do not rewrite working prompts from scratch.
- Test after every phase. `uv run pytest tests/ -q` must stay at 245.
- Respect the kernel/app boundary. Tool dispatch is kernel; tool instructions in prompts are app.
- The prompt packs are markdown files loaded at runtime. Changes take effect on next review.
- The PC Chair prompt is currently the only inline prompt. Everything else is in .md files.
- Run `cd ui && npx vite build` to verify frontend after any schema changes.
