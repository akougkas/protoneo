# Paper Review Application

The Paper Review application is a pre-submission self-assessment tool for academic authors. Upload a manuscript PDF, select the built-in adaptive venue profile or import a venue template, and receive a structured review packet with scores, strengths, weaknesses, and revision guidance.

## How It Works

1. Upload a PDF manuscript.
2. Select **Adaptive Venue** or import a target venue template.
3. Configure model assignments and pipeline settings.
4. The kernel builds a knowledge graph from the manuscript.
5. A panel of reviewer agents runs through structured deliberation.
6. A meta-reviewer produces the final review packet.

## Venue Templates

ProtoNeo v0.1.0 does not ship private venue prompt packs. It ships a generic review harness with an adaptive prompt pack. Users can create local venue profiles by uploading:

- a CFP or author-instructions file in text or Markdown;
- a review form or offline review template;
- a YAML or JSON profile with a `conference` section.

Uploaded templates are converted into reusable profiles and saved under `~/.protoneo/paper_review/profiles/`, outside the repository. The generated profile provides venue name, scope summary, page limit, format hint, anonymity mode, reviewer roles, scoring scales, and preflight checks. The generic prompt pack then injects that venue context into each reviewer role.

## Pipeline Stages

The full pipeline consists of kernel stages followed by application stages:

| # | Stage | Owner | Description |
|---|-------|-------|-------------|
| 1 | metadata | kernel | Extract title, abstract, sections, citations, figures |
| 2 | ontology | kernel | Generate paper-specific entity and edge types |
| 3 | extraction | kernel | Extract entities and relationships by section |
| 4 | coref | kernel | Merge duplicate entities and create aliases |
| 5 | verification | kernel | Audit grounding, completeness, and connectivity |
| 6 | summary | kernel | Build an agent briefing and prune weak graph evidence |
| 7 | independent_review | app | Run parallel review by all panel agents |
| 8 | deliberation | app | Run multi-round challenge and discussion |
| 9 | meta_review | app | Synthesize individual reviews into the final meta-review |

Every stage writes a durable checkpoint. Interrupted sessions can resume from the last completed stage.

## Profile Structure

The built-in profile lives at `apps/paper_review/profiles/adaptive.profile.yaml`. User-generated profiles use the same shape:

```yaml
conference:
  slug: custom-venue
  name: "Custom Venue"
  short_name: "Custom Venue"
scope:
  summary: "Venue scope, criteria, and expectations."
  topics:
    - "Relevant topic"
  must_show_scope_connection: true
submission:
  max_pages_excluding_references: 11
  format: "Venue-specified format"
  dual_anonymous: true
review_form:
  overall_merit:
    scale: [1, 5]
    labels:
      1: "Reject"
      3: "Borderline"
      5: "Strong accept"
panel:
  agents:
    technical:
      role: "Technical Soundness Reviewer"
      focus: ["methodology", "evidence", "correctness"]
    novelty:
      role: "Novelty and Positioning Reviewer"
      focus: ["originality", "related work", "venue fit"]
    clarity:
      role: "Clarity and Presentation Reviewer"
      focus: ["writing", "organization", "reader accessibility"]
    skeptic:
      role: "Adversarial Skeptic"
      focus: ["failure modes", "unsupported claims"]
    meta_reviewer:
      role: "Meta-Reviewer"
      focus: ["synthesis", "score calibration", "revision plan"]
```

## Prompt Templates

The release prompt pack lives in `apps/paper_review/prompts/adaptive/`. It contains shared instructions and role overlays for technical, novelty, clarity, skeptic, artifact, meta-review, and interactive PC-chair workflows. If a custom profile does not have its own prompt directory, the adaptive prompt pack is used automatically.

## API Endpoints

All routes are under `/api/apps/paper_review/`:

```text
GET  /conferences                         # List available built-in and user profiles
GET  /conferences/{slug}                  # Get a specific profile
POST /conferences/from-template           # Create a local profile from a venue template
POST /preflight                           # Run pre-submission checks
POST /review                              # Start a single-paper review session
POST /batch-review                        # Start batch review of multiple papers
POST /review-with-graph                   # Import an existing graph for review
POST /sessions/{id}/launch-review         # Launch review for an existing graph session
POST /sessions/{id}/refine-field          # Refine a specific review field
POST /sessions/{id}/update-final-review   # Update the final review
POST /packet-review                       # Run review from packet folders with offline templates
```

## Export Formats

The app contributes two normal export formats:

- `review-markdown`: Markdown review packet
- `review-pdf`: PDF review packet via WeasyPrint

For packet workflows, ProtoNeo can also fill an offline review text template when a packet folder includes a `*_review.txt` template.

Export through the kernel endpoint:

```text
GET /api/sessions/{id}/export?format=review-markdown
```

## Runtime Artifacts

Generated sessions, graphs, uploaded PDFs, packet outputs, caches, and user-generated profiles are local runtime data. They are intentionally ignored by git or stored outside the repository.
