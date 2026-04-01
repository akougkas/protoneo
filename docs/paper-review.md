# Paper Review Application

The Paper Review application is a pre-submission self-assessment tool for academic authors. Upload a manuscript PDF, choose a target venue, and receive a structured review packet shaped by that conference's criteria, reviewer roles, and scoring scale.

## How It Works

1. **Upload** a PDF manuscript
2. **Select** a target conference (HPDC, SC, or custom)
3. **Configure** model assignments and pipeline settings
4. The kernel **builds a knowledge graph** from the manuscript
5. A **panel of reviewer agents** runs through structured deliberation
6. A **review packet** is produced with scores, strengths, weaknesses, and revision guidance

## Pipeline Stages

The full pipeline consists of kernel stages (graph building) followed by application stages (review deliberation):

| # | Stage | Owner | Description |
|---|-------|-------|-------------|
| 1 | metadata | kernel | Extract title, abstract, sections, citations, figures |
| 2 | ontology | kernel | Generate domain-specific entity and edge types |
| 3 | extraction | kernel | Section-aware entity and relationship extraction |
| 4 | coref | kernel | Merge duplicate entities and create aliases |
| 5 | verification | kernel | 3-pass audit (grounding, completeness, connectivity) |
| 6 | summary | kernel | Agent briefing, structural links, ungrounded pruning |
| 7 | independent_review | app | Parallel review by all panel agents |
| 8 | deliberation | app | Multi-round challenge and discussion |
| 9 | meta_review | app | Synthesis of individual reviews into meta-review |
| 10 | pc_chair | app | Final accept/reject recommendation |

Every stage writes a durable checkpoint. Interrupted sessions resume from the last completed stage.

## Conference Profiles

Conference profiles define reviewer roles, scoring scales, evaluation criteria, and agent configurations. Profiles are YAML files in `apps/paper_review/profiles/`.

### Included Profiles

- **hpdc26**: HPDC 2026 (High Performance Distributed Computing)
- **sc26**: SC 2026 (Supercomputing)

### Profile Structure

```yaml
name: hpdc26
display_name: "HPDC 2026"
track: main

# Scoring scale
score_scale:
  min: 1
  max: 10
  labels:
    1: "Strong Reject"
    5: "Borderline"
    10: "Strong Accept"

# Reviewer roles define the panel composition
agents:
  - role: technical
    focus: "Technical correctness, methodology, experimental design"
    model_preference: reasoning
  - role: novelty
    focus: "Novelty, related work positioning, contribution significance"
  - role: clarity
    focus: "Writing quality, presentation, reproducibility"
  - role: skeptic
    focus: "Weaknesses, missing comparisons, threats to validity"
  - role: meta
    focus: "Synthesize all reviews into a meta-review with final scores"
```

### Creating Custom Profiles

1. Create a new YAML file in `apps/paper_review/profiles/` (e.g., `myconf.profile.yaml`)
2. Define the scoring scale, reviewer roles, and evaluation criteria
3. Create matching prompt templates in `apps/paper_review/prompts/myconf/`
4. The profile appears automatically in the UI conference dropdown

## Prompt Templates

Prompt templates are organized by conference in `apps/paper_review/prompts/{conference}/`. Each conference directory contains:

- `prompt-pack.yaml`: defines which prompt files to use for each reviewer role
- `shared.md`: common context included in all reviewer prompts
- `{role}.md`: role-specific reviewer instructions (e.g., `technical.md`, `novelty.md`)

Templates use Python `str.format()` placeholders:

```markdown
You are reviewing a submission to {conference_name}.

## Paper Context
{paper_summary}

## Knowledge Graph Summary
{graph_summary}

## Your Role
{role_instructions}
```

## Review Packet

The output is a `ReviewPacket` containing:

- **Individual reviews** from each panel agent (scores, strengths, weaknesses, questions)
- **Meta-review** synthesizing all individual reviews
- **Final recommendation** with accept/reject decision
- **Revision plan** with prioritized improvement suggestions

## API Endpoints

All routes are under `/api/apps/paper_review/`:

```
GET  /conferences                    # List available conference profiles
GET  /conferences/{slug}             # Get specific profile
POST /preflight                      # Run pre-submission checks
POST /start-review                   # Start a single-paper review session
POST /batch-review                   # Start batch review of multiple papers
GET  /batch/{id}                     # Get batch status
GET  /batches                        # List all batches
POST /sessions/{id}/launch-review    # Launch review for an existing session
POST /sessions/{id}/refine-field     # Refine a specific review field
POST /sessions/{id}/update-final-review  # Update the final review
```

## Export Formats

The Paper Review app contributes two export formats:

- **review-markdown**: Formatted Markdown review packet
- **review-pdf**: PDF review packet via WeasyPrint

Export through the kernel endpoint: `GET /api/sessions/{id}/export?format=review-markdown`

## Preflight Checks

Before running a full review, the preflight system checks:

- Page limit compliance
- Required section presence (abstract, introduction, related work, etc.)
- Author anonymization (for double-blind venues)
- Venue fit (keyword matching against conference scope)
- Reference quality (minimum count, self-citation ratio)
- Limitation/threat discussion presence

## PDF Processing

PDFs are processed through the kernel's document processing pipeline:

1. `pdf2md` CLI extracts structured markdown (AI-powered with Nemotron-Cascade-2 and Qwen3-VL-30B)
2. Falls back to PyMuPDF plain text extraction if `pdf2md` is unavailable
3. Post-processing strips line number pollution from two-column layouts
4. The resulting `Document.markdown` feeds the knowledge graph pipeline

Set `fast=True` in parse options to skip AI processing and use PyMuPDF only.
