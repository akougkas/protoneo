"""Paper-specific ontology generation for academic review.

Adapted from MiroFish's ontology generator. Instead of social media actors,
this generates an ontology of reviewable concepts for a specific paper.

The ontology runs as Phase 0 before graph extraction. It analyzes the paper
and produces a domain-specific schema of entity types and relationship types
tailored to what reviewers need to evaluate in this particular submission.
"""

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from ..llm.client import LLMClient

logger = logging.getLogger("protoneo.knowledge.paper_ontology")


# ── Ontology schema models ────────────────────────────────

class OntologyAttribute(BaseModel):
    name: str
    type: str = "text"
    description: str = ""


class OntologyEntityType(BaseModel):
    name: str
    description: str
    attributes: list[OntologyAttribute] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class OntologyEdgeType(BaseModel):
    name: str
    description: str
    source_targets: list[dict[str, str]] = Field(default_factory=list)


class PaperOntology(BaseModel):
    """Domain-specific ontology generated for a specific paper."""
    entity_types: list[OntologyEntityType] = Field(default_factory=list)
    edge_types: list[OntologyEdgeType] = Field(default_factory=list)
    analysis_summary: str = ""
    paper_domain: str = ""
    key_contributions: list[str] = Field(default_factory=list)


# ── Base entity types (always included) ────────────────────

_BASE_ENTITY_TYPES = [
    OntologyEntityType(
        name="Claim",
        description="A testable assertion or contribution claim made by the authors",
        attributes=[
            OntologyAttribute(name="stated_in_section", description="Which section states this claim"),
            OntologyAttribute(name="evidence_strength", description="How strongly the paper supports this claim"),
            OntologyAttribute(name="quantified", type="boolean", description="Whether the claim includes quantitative evidence"),
        ],
        examples=["2.3x speedup over baseline", "first system to achieve X"],
    ),
    OntologyEntityType(
        name="Method",
        description="An algorithm, technique, or approach proposed or used",
        attributes=[
            OntologyAttribute(name="novelty_claim", description="Whether the authors claim this method is novel"),
            OntologyAttribute(name="complexity", description="Computational or algorithmic complexity if stated"),
        ],
        examples=["ScaleSort", "adaptive oversampling", "Hilbert curve pivot selection"],
    ),
    OntologyEntityType(
        name="Dataset",
        description="A dataset or benchmark used for evaluation",
        attributes=[
            OntologyAttribute(name="size", description="Number of samples, records, or scale"),
            OntologyAttribute(name="domain", description="Application domain of the dataset"),
            OntologyAttribute(name="availability", description="Public, private, or restricted access"),
        ],
        examples=["ImageNet", "GLUE", "custom synthetic benchmark"],
    ),
    OntologyEntityType(
        name="Metric",
        description="A quantitative measure used to evaluate results",
        attributes=[
            OntologyAttribute(name="value", description="The reported numeric value"),
            OntologyAttribute(name="unit", description="Unit of measurement"),
            OntologyAttribute(name="comparison_baseline", description="What the metric is compared against"),
        ],
        examples=["accuracy", "throughput (GB/s)", "F1 score"],
    ),
    OntologyEntityType(
        name="Baseline",
        description="A prior method used as comparison point",
        attributes=[
            OntologyAttribute(name="source_paper", description="Citation or origin of the baseline"),
            OntologyAttribute(name="comparison_result", description="How the proposed method compares to this baseline"),
        ],
        examples=["RadixSort-MPI", "HykSort", "GPT-3"],
    ),
    OntologyEntityType(
        name="Result",
        description="A specific quantitative outcome or finding",
        attributes=[
            OntologyAttribute(name="metric_value", description="The numeric result with unit"),
            OntologyAttribute(name="conditions", description="Experimental conditions under which the result holds"),
            OntologyAttribute(name="statistical_significance", description="p-value, confidence interval, or similar"),
        ],
        examples=["847 GB/s throughput on 65536 nodes", "95.2% accuracy on test set"],
    ),
    OntologyEntityType(
        name="Limitation",
        description="An acknowledged weakness or scope constraint",
        attributes=[
            OntologyAttribute(name="severity", description="How significantly this limits the contribution"),
            OntologyAttribute(name="acknowledged_by_authors", type="boolean", description="Whether the authors explicitly state this limitation"),
        ],
        examples=["single-machine evaluation only", "assumes uniform data distribution"],
    ),
]

_FALLBACK_ENTITY_TYPES = [
    OntologyEntityType(
        name="Concept",
        description="Any theoretical idea not captured by more specific types",
        attributes=[
            OntologyAttribute(name="formal_definition", description="Mathematical or formal statement if applicable"),
        ],
        examples=["convergence guarantee", "approximation ratio"],
    ),
    OntologyEntityType(
        name="Reference",
        description="A cited prior work from the bibliography",
        attributes=[
            OntologyAttribute(name="citation_key", description="Citation number or author-year key"),
            OntologyAttribute(name="relevance", description="How this reference relates to the paper"),
        ],
        examples=["[1] Dean et al. 2012", "[15] Vaswani et al. 2017"],
    ),
]

_STRUCTURAL_ENTITY_TYPES = [
    OntologyEntityType(
        name="Equation",
        description="A labeled equation, theorem, or lemma",
        attributes=[
            OntologyAttribute(name="label", description="The equation label (Eq. 1, Theorem 2)"),
            OntologyAttribute(name="appears_in_section", description="Which section contains this equation"),
        ],
        examples=["Eq. 1", "Theorem 2", "Lemma 3"],
    ),
]

# ── Base edge types (always included) ─────────────────────

_BASE_EDGE_TYPES = [
    OntologyEdgeType(
        name="USES",
        description="Entity employs or applies another entity",
        source_targets=[{"source": "Method", "target": "Dataset"}, {"source": "Method", "target": "Method"}],
    ),
    OntologyEdgeType(
        name="EVALUATES_ON",
        description="Method or system evaluated using a dataset or benchmark",
        source_targets=[{"source": "Method", "target": "Dataset"}, {"source": "Result", "target": "Dataset"}],
    ),
    OntologyEdgeType(
        name="COMPARED_AGAINST",
        description="Entity compared to a baseline or alternative",
        source_targets=[{"source": "Method", "target": "Baseline"}, {"source": "Result", "target": "Baseline"}],
    ),
    OntologyEdgeType(
        name="ACHIEVES",
        description="Method or system achieves a specific result",
        source_targets=[{"source": "Method", "target": "Result"}, {"source": "Method", "target": "Metric"}],
    ),
    OntologyEdgeType(
        name="EXTENDS",
        description="Entity builds upon or extends prior work",
        source_targets=[{"source": "Method", "target": "Method"}, {"source": "Method", "target": "Baseline"}],
    ),
    OntologyEdgeType(
        name="CITES",
        description="Entity references another work",
        source_targets=[{"source": "Claim", "target": "Reference"}, {"source": "Method", "target": "Reference"}],
    ),
    OntologyEdgeType(
        name="PART_OF",
        description="Entity is a component of another entity",
        source_targets=[{"source": "Method", "target": "Method"}, {"source": "Concept", "target": "Method"}],
    ),
    OntologyEdgeType(
        name="CONTRADICTS",
        description="Entity conflicts with or contradicts another",
        source_targets=[{"source": "Claim", "target": "Claim"}, {"source": "Result", "target": "Result"}],
    ),
]

_STRUCTURAL_EDGE_TYPES = [
    OntologyEdgeType(
        name="HAS_SECTION",
        description="Paper contains this section",
        source_targets=[{"source": "Paper", "target": "Section"}],
    ),
    OntologyEdgeType(
        name="CONTAINS",
        description="Section or entity contains a sub-element",
        source_targets=[{"source": "Paper", "target": "Diagram"}, {"source": "Paper", "target": "Table"}],
    ),
    OntologyEdgeType(
        name="APPEARS_IN",
        description="Entity appears in a specific section",
        source_targets=[{"source": "Concept", "target": "Section"}, {"source": "Method", "target": "Section"}],
    ),
    OntologyEdgeType(
        name="ALIAS_OF",
        description="Entity is an abbreviation or alternative name for another",
        source_targets=[{"source": "Concept", "target": "Concept"}, {"source": "Method", "target": "Method"}],
    ),
]


# ── LLM prompt for ontology generation ────────────────────

_ONTOLOGY_SYSTEM = """\
You are an academic paper ontology designer for a peer review system. Your job
is to analyze a research paper and design a domain-specific schema of entity
types and relationship types that capture everything a reviewer needs to evaluate.

The ontology must help reviewers assess:
- What the paper contributes (novelty, significance)
- How the contributions are implemented (methods, algorithms)
- How they are evaluated (datasets, metrics, baselines)
- What evidence supports the claims (results, figures, tables)
- What limitations exist (gaps, threats to validity)
"""

_ONTOLOGY_USER = """\
Analyze this research paper and design paper-SPECIFIC ontology types for reviewer evaluation.

## Base types (already included, DO NOT duplicate these)

The following entity types are automatically added to every paper ontology.
Do NOT include any of these in your output:
- Claim, Method, Dataset, Metric, Baseline, Result, Limitation
- Concept (catch-all), Reference (cited works), Equation (labeled equations)

The following edge types are also already included:
- USES, EVALUATES_ON, COMPARED_AGAINST, ACHIEVES, EXTENDS, CITES, PART_OF, CONTRADICTS
- HAS_SECTION, CONTAINS, APPEARS_IN, ALIAS_OF

## Your task

Design 3-5 entity types that are SPECIFIC to this paper and not covered by the base types above.
These should capture domain-specific concepts that reviewers need to evaluate.

Examples of good paper-specific types:
- **HardwarePlatform**: GPUs, clusters, testbeds used in evaluation
- **Algorithm**: specific pseudocode procedures or computational steps
- **Model**: machine learning models, statistical models
- **System**: software systems, frameworks, platforms
- **Assumption**: stated or implicit assumptions
- **Hyperparameter**: tuning choices (learning rate, batch size, etc.)
- **Workload**: specific benchmark workloads or test configurations

Also design 3-5 paper-specific edge types not already in the base set.

### Output format (JSON):
{
  "entity_types": [
    {
      "name": "PascalCase name",
      "description": "What this type represents and why reviewers care about it (max 100 chars)",
      "attributes": [
        {"name": "snake_case_name", "type": "text", "description": "what this attribute captures"}
      ],
      "examples": ["example from this paper", "another example"]
    }
  ],
  "edge_types": [
    {
      "name": "UPPER_SNAKE_CASE",
      "description": "What this relationship means (max 100 chars)",
      "source_targets": [
        {"source": "EntityType1", "target": "EntityType2"}
      ]
    }
  ],
  "analysis_summary": "2-3 sentence summary of the paper's domain and what reviewers should focus on",
  "paper_domain": "the research area (e.g., 'distributed systems', 'computer vision', 'NLP')",
  "key_contributions": ["contribution 1", "contribution 2", "contribution 3"]
}

Design 3-5 paper-specific entity types. Base types (Claim, Method, Dataset, Metric, Baseline, \
Result, Limitation, Concept, Reference, Equation) are added automatically.
Each entity type needs 2-3 attributes.

## Paper text:

"""


def _parse_ontology(raw: str) -> PaperOntology:
    """Parse LLM output into PaperOntology."""
    # Try direct JSON
    try:
        data = json.loads(raw)
        return PaperOntology(**data)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Try code fence
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", raw, re.DOTALL)
    if fence:
        try:
            data = json.loads(fence.group(1))
            return PaperOntology(**data)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Try brace matching
    start = raw.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(raw[start : i + 1])
                        return PaperOntology(**data)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        break

    logger.warning("Failed to parse ontology output")
    return PaperOntology()


def _validate_ontology(ontology: PaperOntology) -> PaperOntology:
    """Enforce constraints and merge base + fallback + structural types.

    Keeps LLM-generated types that do not duplicate any base type name.
    Caps LLM-added entity types at 8 and LLM-added edge types at 8.
    Adds all base, fallback, and structural types automatically.
    """
    # Truncate descriptions to 100 chars
    for et in ontology.entity_types:
        if len(et.description) > 100:
            et.description = et.description[:97] + "..."

    for rt in ontology.edge_types:
        if len(rt.description) > 100:
            rt.description = rt.description[:97] + "..."

    # Collect all base/fallback/structural entity type names
    reserved_entity_names = {
        et.name for et in _BASE_ENTITY_TYPES + _FALLBACK_ENTITY_TYPES + _STRUCTURAL_ENTITY_TYPES
    }

    # Keep only LLM-generated types that do not duplicate reserved names
    llm_entity_types = [
        et for et in ontology.entity_types
        if et.name not in reserved_entity_names
    ]

    # Cap LLM-added types at 8
    llm_entity_types = llm_entity_types[:8]

    # Assemble final entity types: base + LLM-specific + fallback + structural
    ontology.entity_types = (
        list(_BASE_ENTITY_TYPES)
        + llm_entity_types
        + list(_FALLBACK_ENTITY_TYPES)
        + list(_STRUCTURAL_ENTITY_TYPES)
    )

    # Collect all base/structural edge type names
    reserved_edge_names = {
        et.name for et in _BASE_EDGE_TYPES + _STRUCTURAL_EDGE_TYPES
    }

    # Keep only LLM-generated edge types that do not duplicate reserved names
    llm_edge_types = [
        et for et in ontology.edge_types
        if et.name not in reserved_edge_names
    ]

    # Cap LLM-added edge types at 8
    llm_edge_types = llm_edge_types[:8]

    # Assemble final edge types: base + LLM-specific + structural
    ontology.edge_types = (
        list(_BASE_EDGE_TYPES)
        + llm_edge_types
        + list(_STRUCTURAL_EDGE_TYPES)
    )

    return ontology


def ontology_to_extraction_prompt(ontology: PaperOntology) -> str:
    """Convert a PaperOntology into a guided extraction prompt.

    This bridges Phase 0 (ontology) to Phase 1 (extraction). The extraction
    LLM uses the ontology schema to know exactly what entity and relationship
    types to extract from the paper.
    """
    entity_section = []
    for et in ontology.entity_types:
        attrs = ", ".join(a.name for a in et.attributes) if et.attributes else "none"
        examples = ", ".join(et.examples[:3]) if et.examples else "none"
        entity_section.append(
            f"- **{et.name}**: {et.description} (attributes: {attrs}) (examples: {examples})"
        )

    edge_section = []
    for rt in ontology.edge_types:
        pairs = ", ".join(f"{st['source']}→{st['target']}" for st in rt.source_targets[:3])
        edge_section.append(f"- **{rt.name}**: {rt.description} ({pairs})")

    return (
        f"## Paper Domain: {ontology.paper_domain}\n\n"
        f"## Key Contributions\n"
        + "\n".join(f"- {c}" for c in ontology.key_contributions)
        + "\n\n## Entity Types to Extract\n"
        + "\n".join(entity_section)
        + "\n\n## Relationship Types to Extract\n"
        + "\n".join(edge_section)
    )


_REFINE_SYSTEM = """\
You are a critical reviewer of ontology schemas for academic paper analysis.
Your job is to challenge and refine an ontology, ensuring it captures everything
a peer reviewer needs to evaluate the paper. Always respond with valid JSON only."""

_REFINE_USER = """\
Below is a draft ontology generated for an academic paper. Challenge and refine it:

1. Are any entity types too vague or overlapping? Merge or sharpen them.
2. Are any important domain-specific types missing? Add them.
3. Do the edge types capture all key relationships a reviewer needs?
4. Are the examples specific enough to guide extraction?

## Draft Ontology:
{draft_json}

## Paper excerpt (for context):
{paper_excerpt}

Return the refined ontology in the same JSON format:
{{"entity_types": [...], "edge_types": [...], "analysis_summary": "...", "paper_domain": "...", "key_contributions": [...]}}

Keep 3-5 entity types and 3-5 edge types. Only return paper-specific types (base types like Claim, Method, Dataset are added automatically)."""


def _build_condensed_text(
    metadata: "PaperMetadata | None",
    full_text: str,
) -> str:
    """Build a condensed paper excerpt for ontology Pass 1.

    Includes the abstract, section headings, Introduction (first 3000 chars),
    Methodology/Design section (first 5000 chars), and Evaluation section
    (first 3000 chars). Falls back to the full text if section_texts is
    empty or the condensed version is under 2000 chars.
    """
    if not metadata:
        return ""

    # Pick the best available section texts (markdown preferred)
    sec_texts = metadata.section_texts_md or metadata.section_texts
    if not sec_texts:
        return ""

    _INTRO_KEYS = {"introduction", "1 introduction", "i introduction", "1. introduction"}
    _METHOD_KEYS = {
        "methodology", "methods", "method", "approach", "design",
        "implementation", "system design", "architecture", "overview",
        "framework", "system", "proposed",
    }
    _EVAL_KEYS = {
        "evaluation", "experiments", "results", "experimental evaluation",
        "experimental results", "performance evaluation", "experimental setup",
        "performance", "analysis",
    }

    def _find_section(target_keys: set[str], max_chars: int) -> str:
        for heading, body in sec_texts.items():
            heading_lower = heading.lower().strip()
            # Strip leading number/dot prefix for matching
            bare = re.sub(r"^[\d.]+\s*", "", heading_lower).strip()
            if bare in target_keys or heading_lower in target_keys:
                return body[:max_chars]
            # Partial match: check if any target key appears as a substring
            for key in target_keys:
                if key in bare:
                    return body[:max_chars]
        return ""

    parts: list[str] = []

    # Abstract
    if metadata.abstract:
        parts.append(f"## Abstract\n{metadata.abstract}")

    # Section headings
    if metadata.sections:
        parts.append(f"## Sections\n{', '.join(metadata.sections)}")

    # Introduction
    intro = _find_section(_INTRO_KEYS, 3000)
    if intro:
        parts.append(f"## Introduction\n{intro}")

    # Methodology / Design
    method = _find_section(_METHOD_KEYS, 5000)
    if method:
        parts.append(f"## Methodology\n{method}")

    # Evaluation
    evaluation = _find_section(_EVAL_KEYS, 3000)
    if evaluation:
        parts.append(f"## Evaluation\n{evaluation}")

    condensed = "\n\n".join(parts)

    # If we only got the abstract and headings (no body sections matched),
    # append the first 8000 chars of the full text as fallback material
    if len(condensed) < 4000 and full_text:
        condensed += f"\n\n## Paper Body (excerpt)\n{full_text[:8000]}"

    # Fall back to full text if condensed version is still too thin
    if len(condensed) < 2000:
        return ""

    return condensed


async def generate_paper_ontology(
    text: str,
    llm_client: LLMClient,
    model: str = "",
    session_id: str | None = None,
    conference_context: str = "",
    metadata: "PaperMetadata | None" = None,
    markdown: str = "",
) -> PaperOntology:
    """Generate a domain-specific ontology for an academic paper.

    This is Phase 0 of the review pipeline. The ontology tells the graph
    extractor what entity types and relationships are relevant for this
    specific paper, similar to how MiroFish generates ontology before
    building the knowledge graph.

    Uses a two-pass approach: Pass 1 generates the initial ontology,
    Pass 2 challenges and refines it with the same model.

    When metadata is provided, the LLM receives the paper's structural
    summary (title, sections, figure/table counts, reference count) which
    produces significantly better ontology types than raw text alone.

    Args:
        text: Full paper text (no truncation applied).
        llm_client: LLM client for the generation call.
        model: Which model to use.
        session_id: Optional session ID for cost tracking.
        conference_context: Optional venue context (e.g., "HPC conference").
        metadata: Optional PaperMetadata for structural context.
        markdown: Optional structured markdown from Docling.

    Returns:
        PaperOntology with entity types, edge types, and analysis.
    """
    # Prefer markdown over flat text when available
    paper_text = markdown if markdown else text

    # Build metadata preamble so the LLM knows the paper's structure
    meta_preamble = ""
    if metadata:
        meta_parts = []
        if metadata.title:
            meta_parts.append(f"Title: {metadata.title}")
        if metadata.abstract:
            meta_parts.append(f"Abstract: {metadata.abstract[:500]}")
        if metadata.sections:
            meta_parts.append(f"Sections ({len(metadata.sections)}): {', '.join(metadata.sections)}")
        meta_parts.append(f"Figures: {metadata.figure_count}, Tables: {metadata.table_count}, References: {metadata.reference_count}")
        meta_parts.append(f"Estimated words: {metadata.estimated_word_count}")
        meta_preamble = "## Paper Structure (extracted from PDF)\n\n" + "\n".join(meta_parts) + "\n\n"

    # Build a condensed version of the paper for Pass 1 instead of sending
    # the full 60-80k char document. The ontology generator only needs to
    # understand the domain, contributions, and evaluation approach.
    condensed_text = _build_condensed_text(metadata, paper_text)
    pass1_text = condensed_text if condensed_text else paper_text

    user_content = meta_preamble + _ONTOLOGY_USER + pass1_text
    if conference_context:
        user_content += f"\n\n## Conference Context\n{conference_context}"

    messages = [
        {"role": "system", "content": _ONTOLOGY_SYSTEM},
        {"role": "user", "content": user_content},
    ]

    # Pass 1: Generate initial ontology
    response = await llm_client.complete(
        model=model,
        messages=messages,
        session_id=session_id,
        temperature=0.3,
        max_tokens=16384,
    )

    draft_ontology = _parse_ontology(response.content)

    # Pass 2: Challenge and refine with the same model
    try:
        draft_json = json.dumps({
            "entity_types": [et.model_dump() for et in draft_ontology.entity_types],
            "edge_types": [rt.model_dump() for rt in draft_ontology.edge_types],
            "analysis_summary": draft_ontology.analysis_summary,
            "paper_domain": draft_ontology.paper_domain,
            "key_contributions": draft_ontology.key_contributions,
        }, indent=2)

        # Use a shorter paper excerpt for the refinement pass
        paper_excerpt = paper_text[:8000]

        refine_messages = [
            {"role": "system", "content": _REFINE_SYSTEM},
            {"role": "user", "content": _REFINE_USER.format(
                draft_json=draft_json, paper_excerpt=paper_excerpt,
            )},
        ]

        refine_response = await llm_client.complete(
            model=model,
            messages=refine_messages,
            session_id=session_id,
            temperature=0.2,
            max_tokens=16384,
        )

        refined = _parse_ontology(refine_response.content)
        if refined.entity_types or refined.edge_types:
            # Merge refinements: prefer refined fields if non-empty
            if refined.entity_types:
                draft_ontology.entity_types = refined.entity_types
            if refined.edge_types:
                draft_ontology.edge_types = refined.edge_types
            if refined.analysis_summary:
                draft_ontology.analysis_summary = refined.analysis_summary
            if refined.paper_domain:
                draft_ontology.paper_domain = refined.paper_domain
            if refined.key_contributions:
                draft_ontology.key_contributions = refined.key_contributions
            logger.info("Ontology refined in pass 2")
    except Exception as e:
        logger.warning("Ontology refinement pass failed, using pass 1 result: %s", e)

    ontology = _validate_ontology(draft_ontology)

    logger.info(
        "Generated ontology: %d entity types, %d edge types, domain=%s",
        len(ontology.entity_types),
        len(ontology.edge_types),
        ontology.paper_domain,
    )

    return ontology
