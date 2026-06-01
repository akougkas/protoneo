"""Multi-step ontology generation for domain-specific knowledge graphs.

Generates a schema of entity types and relationship types tailored to a
specific document, using LLM-driven discovery with self-consistency and
grounding verification.

Architecture (inspired by ODKE+, OntoKGen, Ontogenia ESWC 2024):

  Step 1: Domain Detection + Pattern Seeding (no LLM, fast)
    Classify document domain from abstract keywords, load seed pattern.

  Step 2: Ontology Discovery (LLM, self-consistent)
    Run N parallel generations, keep types appearing in >=2 of N samples.

  Step 3: Ontology Grounding (LLM, focused)
    Verify each candidate type has concrete examples in the document.
    Reject ungrounded types.

  Step 4: Merge + Validate (no LLM)
    Merge grounded types with base types, generate extraction prompt.

Runs as Phase 0 before graph extraction. The resulting ontology tells the
extractor what entity and relationship types to extract, producing a more
focused and grounded knowledge graph.
"""

import asyncio
import json
import logging
import re
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from ..llm.client import LLMClient
from ..llm.structured import extract_json_object, sanitize_structured_text
from .types import DomainConfig

logger = logging.getLogger("protoneo.knowledge.ontology")


# ── Ontology schema models ────────────────────────────────

class OntologyAttribute(BaseModel):
    name: str
    type: str = "text"
    description: str = ""


class EntityType(BaseModel):
    name: str
    description: str
    attributes: list[OntologyAttribute] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class EdgeType(BaseModel):
    name: str
    description: str
    source_targets: list[dict[str, str]] = Field(default_factory=list)


class Ontology(BaseModel):
    """Domain-specific ontology generated for a specific document."""
    entity_types: list[EntityType] = Field(default_factory=list)
    edge_types: list[EdgeType] = Field(default_factory=list)
    analysis_summary: str = ""
    paper_domain: str = ""
    key_contributions: list[str] = Field(default_factory=list)


# ── Base entity types (always included) ────────────────────

_BASE_ENTITY_TYPES = [
    EntityType(
        name="Claim",
        description="A testable assertion or contribution claim made by the authors",
        attributes=[
            OntologyAttribute(name="stated_in_section", description="Which section states this claim"),
            OntologyAttribute(name="evidence_strength", description="How strongly the paper supports this claim"),
            OntologyAttribute(name="quantified", type="boolean", description="Whether the claim includes quantitative evidence"),
        ],
        examples=["2.3x speedup over baseline", "first system to achieve X"],
    ),
    EntityType(
        name="Method",
        description="An algorithm, technique, or approach proposed or used",
        attributes=[
            OntologyAttribute(name="novelty_claim", description="Whether the authors claim this method is novel"),
            OntologyAttribute(name="complexity", description="Computational or algorithmic complexity if stated"),
        ],
        examples=["ScaleSort", "adaptive oversampling", "Hilbert curve pivot selection"],
    ),
    EntityType(
        name="Dataset",
        description="A dataset or benchmark used for evaluation",
        attributes=[
            OntologyAttribute(name="size", description="Number of samples, records, or scale"),
            OntologyAttribute(name="domain", description="Application domain of the dataset"),
            OntologyAttribute(name="availability", description="Public, private, or restricted access"),
        ],
        examples=["ImageNet", "GLUE", "custom synthetic benchmark"],
    ),
    EntityType(
        name="Metric",
        description="A quantitative measure used to evaluate results",
        attributes=[
            OntologyAttribute(name="value", description="The reported numeric value"),
            OntologyAttribute(name="unit", description="Unit of measurement"),
            OntologyAttribute(name="comparison_baseline", description="What the metric is compared against"),
        ],
        examples=["accuracy", "throughput (GB/s)", "F1 score"],
    ),
    EntityType(
        name="Baseline",
        description="A prior method used as comparison point",
        attributes=[
            OntologyAttribute(name="source_paper", description="Citation or origin of the baseline"),
            OntologyAttribute(name="comparison_result", description="How the proposed method compares to this baseline"),
        ],
        examples=["RadixSort-MPI", "HykSort", "GPT-3"],
    ),
    EntityType(
        name="Result",
        description="A specific quantitative outcome or finding",
        attributes=[
            OntologyAttribute(name="metric_value", description="The numeric result with unit"),
            OntologyAttribute(name="conditions", description="Experimental conditions under which the result holds"),
            OntologyAttribute(name="statistical_significance", description="p-value, confidence interval, or similar"),
        ],
        examples=["847 GB/s throughput on 65536 nodes", "95.2% accuracy on test set"],
    ),
    EntityType(
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
    EntityType(
        name="Concept",
        description="Any theoretical idea not captured by more specific types",
        attributes=[
            OntologyAttribute(name="formal_definition", description="Mathematical or formal statement if applicable"),
        ],
        examples=["convergence guarantee", "approximation ratio"],
    ),
    EntityType(
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
    EntityType(
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
    EdgeType(
        name="USES",
        description="Entity employs or applies another entity",
        source_targets=[{"source": "Method", "target": "Dataset"}, {"source": "Method", "target": "Method"}],
    ),
    EdgeType(
        name="EVALUATES_ON",
        description="Method or system evaluated using a dataset or benchmark",
        source_targets=[{"source": "Method", "target": "Dataset"}, {"source": "Result", "target": "Dataset"}],
    ),
    EdgeType(
        name="COMPARED_AGAINST",
        description="Entity compared to a baseline or alternative",
        source_targets=[{"source": "Method", "target": "Baseline"}, {"source": "Result", "target": "Baseline"}],
    ),
    EdgeType(
        name="ACHIEVES",
        description="Method or system achieves a specific result",
        source_targets=[{"source": "Method", "target": "Result"}, {"source": "Method", "target": "Metric"}],
    ),
    EdgeType(
        name="EXTENDS",
        description="Entity builds upon or extends prior work",
        source_targets=[{"source": "Method", "target": "Method"}, {"source": "Method", "target": "Baseline"}],
    ),
    EdgeType(
        name="CITES",
        description="Entity references another work",
        source_targets=[{"source": "Claim", "target": "Reference"}, {"source": "Method", "target": "Reference"}],
    ),
    EdgeType(
        name="PART_OF",
        description="Entity is a component of another entity",
        source_targets=[{"source": "Method", "target": "Method"}, {"source": "Concept", "target": "Method"}],
    ),
    EdgeType(
        name="CONTRADICTS",
        description="Entity conflicts with or contradicts another",
        source_targets=[{"source": "Claim", "target": "Claim"}, {"source": "Result", "target": "Result"}],
    ),
]

_STRUCTURAL_EDGE_TYPES = [
    EdgeType(
        name="HAS_SECTION",
        description="Paper contains this section",
        source_targets=[{"source": "Paper", "target": "Section"}],
    ),
    EdgeType(
        name="CONTAINS",
        description="Section or entity contains a sub-element",
        source_targets=[{"source": "Paper", "target": "Diagram"}, {"source": "Paper", "target": "Table"}],
    ),
    EdgeType(
        name="APPEARS_IN",
        description="Entity appears in a specific section",
        source_targets=[{"source": "Concept", "target": "Section"}, {"source": "Method", "target": "Section"}],
    ),
    EdgeType(
        name="ALIAS_OF",
        description="Entity is an abbreviation or alternative name for another",
        source_targets=[{"source": "Concept", "target": "Concept"}, {"source": "Method", "target": "Method"}],
    ),
]


# ══════════════════════════════════════════════════════════
# Step 1: Domain Detection + Pattern Seeding
# ══════════════════════════════════════════════════════════

# Domain patterns: seed entity types for common research areas.
# The LLM receives these as starting points, not constraints.
_DOMAIN_PATTERNS: dict[str, list[dict[str, str]]] = {
    "systems": [
        {"name": "HardwarePlatform", "description": "Physical hardware (GPUs, clusters, FPGAs, wearables, sensors) used in experiments"},
        {"name": "SystemConfiguration", "description": "Runtime parameters, deployment settings, or hardware configurations"},
        {"name": "Workload", "description": "Specific benchmark workloads, traces, or test scenarios"},
        {"name": "Optimization", "description": "A specific optimization technique (caching, prefetching, scheduling, pruning)"},
    ],
    "ml": [
        {"name": "Model", "description": "A specific ML/DL model architecture (transformer, CNN, LSTM, etc.)"},
        {"name": "Hyperparameter", "description": "Tuning choices that affect training/inference (learning rate, batch size, etc.)"},
        {"name": "TrainingProcedure", "description": "Training protocol (pretraining, fine-tuning, distillation, augmentation)"},
        {"name": "LossFunction", "description": "Objective function optimized during training"},
    ],
    "networking": [
        {"name": "Protocol", "description": "Communication protocol or standard (TCP, RDMA, MPI, etc.)"},
        {"name": "NetworkTopology", "description": "Network layout (fat-tree, dragonfly, torus) or configuration"},
        {"name": "TrafficPattern", "description": "Communication pattern or workload (all-to-all, nearest-neighbor, etc.)"},
    ],
    "iot": [
        {"name": "SensorType", "description": "Physical sensor hardware (accelerometer, gyroscope, magnetometer, etc.)"},
        {"name": "EnergyModel", "description": "Power consumption model or energy budget constraint"},
        {"name": "ActivityContext", "description": "User activity or environmental context that drives system behavior"},
        {"name": "DevicePlatform", "description": "Wearable or IoT device (smartwatch, sensor node, edge gateway)"},
    ],
    "storage": [
        {"name": "StorageSystem", "description": "File system, object store, or storage backend"},
        {"name": "IOPattern", "description": "I/O access pattern (sequential, random, strided, etc.)"},
        {"name": "DataFormat", "description": "Data representation format (HDF5, Parquet, compressed, etc.)"},
    ],
    "theory": [
        {"name": "Theorem", "description": "A formal theorem, lemma, or proposition with proof"},
        {"name": "Assumption", "description": "Stated or implicit assumption required for results to hold"},
        {"name": "Bound", "description": "Theoretical bound (upper, lower, approximation ratio)"},
    ],
}

# Keywords that map to domain patterns
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "systems": ["hpc", "parallel", "distributed", "cluster", "gpu", "accelerat", "kernel", "runtime", "scheduler", "fpga"],
    "ml": ["neural", "deep learning", "transformer", "training", "fine-tun", "pretrain", "classification", "regression", "reinforcement"],
    "networking": ["network", "protocol", "bandwidth", "latency", "tcp", "rdma", "mpi", "routing", "congestion"],
    "iot": ["iot", "sensor", "wearable", "energy efficien", "battery", "edge computing", "sampling", "accelerometer", "gyroscope", "smart"],
    "storage": ["storage", "file system", "i/o", "compression", "lossy", "hdf5", "checkpoint", "data reduction"],
    "theory": ["theorem", "proof", "lemma", "bound", "complexity", "np-hard", "approximation", "convergence"],
}


def _detect_domain(
    abstract: str,
    sections: list[str],
    domain_config: DomainConfig | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Classify paper domain from abstract keywords and return seed pattern.

    Returns (domain_name, seed_types). If no strong match, returns ("general", []).
    Uses domain_config patterns/keywords when provided, falls back to module defaults.
    """
    patterns = domain_config.domain_patterns if domain_config else _DOMAIN_PATTERNS
    keywords = domain_config.domain_keywords if domain_config else _DOMAIN_KEYWORDS

    text = (abstract + " " + " ".join(sections)).lower()
    scores: Counter = Counter()

    for domain, kw_list in keywords.items():
        for kw in kw_list:
            count = text.count(kw)
            if count > 0:
                scores[domain] += count

    if not scores:
        return "general", []

    # Take top domain, but only if it has >=3 keyword hits
    best_domain, best_score = scores.most_common(1)[0]
    if best_score < 3:
        return "general", []

    seed = patterns.get(best_domain, [])
    logger.info("Domain detected: %s (score=%d, seed types=%d)", best_domain, best_score, len(seed))
    return best_domain, seed


# ══════════════════════════════════════════════════════════
# Step 2: Ontology Discovery (self-consistent parallel)
# ══════════════════════════════════════════════════════════

_DISCOVER_SYSTEM = """\
You are an academic paper ontology designer. Analyze a research paper and design
domain-specific entity types and edge types that capture what peer reviewers need
to evaluate. Output concise valid JSON only. Do not emit hidden reasoning,
scratchpad, markdown, code fences, or prose."""

_DISCOVER_USER = """\
Design 3-4 paper-SPECIFIC entity types for reviewer evaluation of this paper.

## Base types (DO NOT duplicate)
Entity: Claim, Method, Dataset, Metric, Baseline, Result, Limitation, Concept, Reference, Equation
Edge: USES, EVALUATES_ON, COMPARED_AGAINST, ACHIEVES, EXTENDS, CITES, PART_OF, CONTRADICTS

{seed_section}
## Instructions

Design types that capture domain concepts the base types miss. Each type needs:
- A clear name (PascalCase, e.g., "SensorType" not "Sensor_Type")
- A short description of what it represents and why reviewers care
- 1-2 concrete examples from THIS paper

Also design 1-3 paper-specific edge types not in the base set.

Hard limits:
- Keep every description under 12 words.
- Keep every example under 6 words.
- Keep key_contributions to at most 3 items.
- Finish a valid JSON object. If space is tight, omit optional items rather than truncating.

Output JSON:
{{"entity_types": [{{"name": "...", "description": "...", "examples": ["from this paper"]}}], "edge_types": [{{"name": "UPPER_CASE", "description": "...", "source_targets": [{{"source": "Type1", "target": "Type2"}}]}}], "paper_domain": "...", "key_contributions": ["..."], "analysis_summary": "..."}}

## Paper:

{paper_text}"""


def _build_focused_text(
    metadata: Any | None,
    markdown: str,
    full_text: str,
) -> str:
    """Build a focused paper excerpt for ontology discovery.

    Includes abstract, introduction, methodology, and evaluation excerpts.
    Targets ~12K chars to fit comfortably in 30B model context while covering
    all sections the ontology needs to see.
    """
    source = markdown if markdown else full_text
    if not source:
        return full_text[:15000] if full_text else ""

    parts: list[str] = []
    total = 0
    budget = 14000

    # Split markdown by ## headers
    sections: list[tuple[str, str]] = []
    current_heading = ""
    current_body: list[str] = []
    for line in source.split("\n"):
        if line.startswith("## "):
            if current_heading or current_body:
                sections.append((current_heading, "\n".join(current_body)))
            current_heading = line.lstrip("# ").strip()
            current_body = []
        else:
            current_body.append(line)
    if current_heading or current_body:
        sections.append((current_heading, "\n".join(current_body)))

    # Priority allocation: Abstract full, Intro 3K, Method 4K, Eval 3K, rest 2K
    priority = [
        ({"abstract"}, 2000),
        ({"introduction"}, 3000),
        ({"methodology", "methods", "method", "approach", "design", "system",
          "framework", "architecture", "proposed", "overview"}, 4000),
        ({"evaluation", "experiments", "results", "experimental", "performance"}, 3000),
    ]

    used_headings: set[str] = set()
    for target_keys, char_limit in priority:
        for heading, body in sections:
            bare = re.sub(r"^[\d.]+\s*", "", heading.lower()).strip()
            if any(k in bare for k in target_keys) and heading not in used_headings:
                excerpt = body[:char_limit]
                parts.append(f"## {heading}\n{excerpt}")
                total += len(excerpt)
                used_headings.add(heading)
                break

    # Fill remaining budget with other sections (Related Work, Conclusion, etc.)
    for heading, body in sections:
        if heading not in used_headings and total < budget:
            remaining = min(1500, budget - total)
            if remaining > 200:
                parts.append(f"## {heading}\n{body[:remaining]}")
                total += min(len(body), remaining)
                used_headings.add(heading)

    result = "\n\n".join(parts)

    # Fallback if sections parsing failed
    if len(result) < 3000:
        return source[:budget]

    return result


async def _discover_ontology_single(
    paper_text: str,
    seed_section: str,
    llm_client: LLMClient,
    model: str,
    session_id: str | None,
    temperature: float,
    discovery_prompt: str = "",
) -> Ontology:
    """Single ontology discovery call."""
    prompt = discovery_prompt or _DISCOVER_USER
    user_content = prompt.format(
        seed_section=seed_section,
        paper_text=paper_text,
    )
    response = await llm_client.complete(
        model=model,
        messages=[
            {"role": "system", "content": _DISCOVER_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        session_id=session_id,
        temperature=temperature,
        max_tokens=3072,
        phase_policy="fast_structured",
    )
    return _parse_ontology(response.content)


async def _discover_with_consistency(
    paper_text: str,
    seed_section: str,
    llm_client: LLMClient,
    model: str,
    session_id: str | None,
    n_samples: int = 3,
    discovery_prompt: str = "",
) -> Ontology:
    """Run N parallel ontology discoveries, keep types that appear in >=2 samples.

    Self-consistency (RSC Digital Discovery 2026): types appearing across
    multiple samples are well-grounded; singletons are likely hallucinated.
    """
    tasks = [
        _discover_ontology_single(
            paper_text, seed_section, llm_client, model, session_id,
            temperature=0.4 + 0.1 * i,  # Vary temperature slightly
            discovery_prompt=discovery_prompt,
        )
        for i in range(n_samples)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect all generated types across samples
    entity_votes: Counter = Counter()
    entity_map: dict[str, EntityType] = {}
    edge_votes: Counter = Counter()
    edge_map: dict[str, EdgeType] = {}
    domains: list[str] = []
    contributions: list[list[str]] = []
    summaries: list[str] = []

    successful = 0
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Ontology discovery sample failed: %s", r)
            continue
        if not r.entity_types and not r.edge_types:
            logger.warning("Ontology discovery sample returned empty")
            continue
        successful += 1

        for et in r.entity_types:
            key = et.name.lower().replace(" ", "")
            entity_votes[key] += 1
            # Keep the version with the most examples
            if key not in entity_map or len(et.examples) > len(entity_map[key].examples):
                entity_map[key] = et

        for rt in r.edge_types:
            key = rt.name.upper()
            edge_votes[key] += 1
            if key not in edge_map or len(rt.description) > len(edge_map[key].description):
                edge_map[key] = rt

        if r.paper_domain:
            domains.append(r.paper_domain)
        if r.key_contributions:
            contributions.append(r.key_contributions)
        if r.analysis_summary:
            summaries.append(r.analysis_summary)

    if successful == 0:
        logger.warning("All %d ontology discovery samples failed", n_samples)
        return Ontology()

    # Threshold: if all 3 succeed, require 2/3 majority.
    # If only 1-2 succeed, accept any type that appeared (better than nothing).
    threshold = 2 if successful >= 3 else 1

    confirmed_entities = [
        entity_map[key] for key, count in entity_votes.most_common()
        if count >= threshold and key in entity_map
    ]
    confirmed_edges = [
        edge_map[key] for key, count in edge_votes.most_common()
        if count >= threshold and key in edge_map
    ]

    # Pick the most common domain
    domain = Counter(domains).most_common(1)[0][0] if domains else ""
    # Merge contributions from all samples, deduplicate
    all_contribs = []
    seen_contribs: set[str] = set()
    for clist in contributions:
        for c in clist:
            c_lower = c.lower().strip()
            if c_lower not in seen_contribs:
                all_contribs.append(c)
                seen_contribs.add(c_lower)
    # Take the longest summary
    summary = max(summaries, key=len) if summaries else ""

    logger.info(
        "Ontology discovery: %d/%d samples succeeded, %d entity types (of %d candidates), %d edge types confirmed",
        successful, n_samples,
        len(confirmed_entities), len(entity_map),
        len(confirmed_edges),
    )

    return Ontology(
        entity_types=confirmed_entities,
        edge_types=confirmed_edges,
        analysis_summary=summary,
        paper_domain=domain,
        key_contributions=all_contribs[:5],
    )


# ══════════════════════════════════════════════════════════
# Step 3: Ontology Grounding
# ══════════════════════════════════════════════════════════

_GROUND_SYSTEM = """\
You verify whether proposed entity types are actually present in a research paper.
For each type, find 2-3 concrete named examples from the paper text.
If a type has fewer than 2 real examples, mark it as ungrounded.
Output concise valid JSON only. Do not emit hidden reasoning, scratchpad,
markdown, code fences, or prose."""

_GROUND_USER = """\
Verify these proposed entity types against the paper. For each type, find concrete
named examples that actually appear in the text. If a type has <2 real examples,
set "grounded": false.

## Proposed types:
{types_json}

## Paper text:
{paper_text}

Output JSON:
{{"verified_types": [{{"name": "TypeName", "grounded": true, "examples": ["Example 1 from paper", "Example 2"], "description": "refined description based on actual examples"}}]}}"""


async def _ground_ontology(
    ontology: Ontology,
    paper_text: str,
    llm_client: LLMClient,
    model: str,
    session_id: str | None,
    grounding_prompt: str = "",
) -> Ontology:
    """Verify each candidate type has concrete examples in the paper.

    Rejects types the LLM cannot find at least 2 examples for.
    This is the grounding step inspired by ODKE+ (Apple, 2025).
    """
    if not ontology.entity_types:
        return ontology

    types_json = json.dumps([
        {"name": et.name, "description": et.description, "examples": et.examples[:3]}
        for et in ontology.entity_types
    ], indent=2)

    prompt = grounding_prompt or _GROUND_USER
    user_content = prompt.format(
        types_json=types_json,
        paper_text=paper_text[:12000],  # Focused excerpt
    )

    try:
        response = await llm_client.complete(
            model=model,
            messages=[
                {"role": "system", "content": _GROUND_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            session_id=session_id,
            temperature=0.15,
            max_tokens=4096,
            phase_policy="fast_structured",
        )

        result = _parse_ontology_grounding(response.content)
        if not result:
            logger.warning("Grounding parse failed, keeping all candidate types")
            return ontology

        # Filter to grounded types only
        grounded_names = {v["name"] for v in result if v.get("grounded", False)}
        grounded_types = []
        for et in ontology.entity_types:
            if et.name in grounded_names:
                # Update examples from grounding if available
                verified = next((v for v in result if v["name"] == et.name), None)
                if verified and verified.get("examples"):
                    et.examples = verified["examples"][:5]
                if verified and verified.get("description"):
                    et.description = verified["description"]
                grounded_types.append(et)

        rejected = [et.name for et in ontology.entity_types if et.name not in grounded_names]
        if rejected:
            logger.info("Grounding rejected %d types: %s", len(rejected), rejected)

        ontology.entity_types = grounded_types
        logger.info("Grounding confirmed %d/%d types", len(grounded_types), len(grounded_names | set(rejected)))

    except Exception as e:
        logger.warning("Ontology grounding failed, keeping all candidate types: %s", e)

    return ontology


def _parse_ontology_grounding(raw: str) -> list[dict[str, Any]] | None:
    """Parse the grounding verification response."""
    def _coerce_verified(value: Any) -> list[dict[str, Any]] | None:
        if not isinstance(value, list):
            return None
        out: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("type") or "").strip()
            if name:
                out.append({**item, "name": name})
        return out

    parsed = extract_json_object(
        raw,
        required_keys={"verified_types"},
        allow_thinking_json=True,
    )
    if parsed is not None and isinstance(parsed.get("verified_types"), list):
        return _coerce_verified(parsed["verified_types"])

    cleaned = sanitize_structured_text(raw)

    # Try direct JSON
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict) and "verified_types" in data:
            return _coerce_verified(data["verified_types"])
        if isinstance(data, list):
            return _coerce_verified(data)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Try code fence
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", cleaned, re.DOTALL)
    if fence:
        try:
            data = json.loads(fence.group(1))
            if isinstance(data, dict) and "verified_types" in data:
                return _coerce_verified(data["verified_types"])
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Brace matching for largest JSON
    start = cleaned.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(cleaned[start:i+1])
                        if isinstance(data, dict) and "verified_types" in data:
                            return _coerce_verified(data["verified_types"])
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
                    break

    return None


# ══════════════════════════════════════════════════════════
# DomainConfig -> Pydantic model converters
# ══════════════════════════════════════════════════════════

def _seeds_to_entity_types(seeds: list) -> list[EntityType]:
    """Convert SeedEntity dataclasses to EntityType models."""
    from .types import SeedEntity
    result = []
    for s in seeds:
        if isinstance(s, SeedEntity):
            result.append(EntityType(
                name=s.name,
                description=s.description,
                attributes=[OntologyAttribute(**a) for a in s.attributes],
                examples=s.examples,
            ))
        elif isinstance(s, EntityType):
            result.append(s)
    return result


def _seeds_to_edge_types(seeds: list) -> list[EdgeType]:
    """Convert SeedEdge dataclasses to EdgeType models."""
    from .types import SeedEdge
    result = []
    for s in seeds:
        if isinstance(s, SeedEdge):
            result.append(EdgeType(
                name=s.name,
                description=s.description,
                source_targets=s.source_targets,
            ))
        elif isinstance(s, EdgeType):
            result.append(s)
    return result


def _seed_ontology(domain: str, seed_types: list[dict[str, str]]) -> Ontology:
    return Ontology(
        entity_types=[
            EntityType(
                name=str(seed.get("name") or "").strip(),
                description=str(seed.get("description") or "").strip(),
            )
            for seed in seed_types
            if str(seed.get("name") or "").strip()
        ],
        paper_domain=domain,
    )


# ══════════════════════════════════════════════════════════
# Step 4: Merge + Validate
# ══════════════════════════════════════════════════════════

def _validate_ontology(
    ontology: Ontology,
    domain_config: DomainConfig | None = None,
) -> Ontology:
    """Enforce constraints and merge base + LLM-generated + structural types.

    Keeps LLM-generated types that do not duplicate any base type name.
    Caps LLM-added entity types at 8 and LLM-added edge types at 8.
    Adds all base, fallback, and structural types automatically.
    Uses domain_config seed types when provided, module defaults otherwise.
    """
    if domain_config and domain_config.base_entity_types:
        base_e = _seeds_to_entity_types(domain_config.base_entity_types)
        fallback_e = _seeds_to_entity_types(domain_config.fallback_entity_types)
        structural_e = _seeds_to_entity_types(domain_config.structural_entity_types)
        base_r = _seeds_to_edge_types(domain_config.base_edge_types)
        structural_r = _seeds_to_edge_types(domain_config.structural_edge_types)
    else:
        base_e = _BASE_ENTITY_TYPES
        fallback_e = _FALLBACK_ENTITY_TYPES
        structural_e = _STRUCTURAL_ENTITY_TYPES
        base_r = _BASE_EDGE_TYPES
        structural_r = _STRUCTURAL_EDGE_TYPES

    for et in ontology.entity_types:
        if len(et.description) > 100:
            et.description = et.description[:97] + "..."

    for rt in ontology.edge_types:
        if len(rt.description) > 100:
            rt.description = rt.description[:97] + "..."

    reserved_entity_names = {
        et.name for et in base_e + fallback_e + structural_e
    }

    llm_entity_types = [
        et for et in ontology.entity_types
        if et.name not in reserved_entity_names
    ][:8]

    ontology.entity_types = (
        list(base_e)
        + llm_entity_types
        + list(fallback_e)
        + list(structural_e)
    )

    reserved_edge_names = {
        et.name for et in base_r + structural_r
    }

    llm_edge_types = [
        et for et in ontology.edge_types
        if et.name not in reserved_edge_names
    ][:8]

    ontology.edge_types = (
        list(base_r)
        + llm_edge_types
        + list(structural_r)
    )

    return ontology


# ══════════════════════════════════════════════════════════
# Ontology -> Extraction Prompt
# ══════════════════════════════════════════════════════════

def ontology_to_extraction_prompt(ontology: Ontology) -> str:
    """Convert an Ontology into a guided extraction prompt.

    Bridges Phase 0 (ontology) to Phase 1 (extraction). The extraction
    LLM uses the ontology schema to know exactly what entity and relationship
    types to extract from the document.
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


# ══════════════════════════════════════════════════════════
# Parsing helpers
# ══════════════════════════════════════════════════════════

def _coerce_ontology_data(data: Any) -> Ontology:
    """Build an ontology while dropping only malformed type records."""
    if not isinstance(data, dict):
        return Ontology()

    entity_types: list[EntityType] = []
    for item in data.get("entity_types") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        attrs: list[OntologyAttribute] = []
        for attr in item.get("attributes") or []:
            if not isinstance(attr, dict):
                continue
            attr_name = str(attr.get("name") or "").strip()
            if attr_name:
                attrs.append(OntologyAttribute(
                    name=attr_name,
                    type=str(attr.get("type") or "text"),
                    description=str(attr.get("description") or ""),
                ))
        examples = item.get("examples") or []
        if isinstance(examples, str):
            examples = [examples]
        entity_types.append(EntityType(
            name=name,
            description=str(item.get("description") or name),
            attributes=attrs,
            examples=[str(x) for x in examples if str(x).strip()],
        ))

    edge_types: list[EdgeType] = []
    for item in data.get("edge_types") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        source_targets = []
        for pair in item.get("source_targets") or []:
            if not isinstance(pair, dict):
                continue
            source = str(pair.get("source") or "").strip()
            target = str(pair.get("target") or "").strip()
            if source and target:
                source_targets.append({"source": source, "target": target})
        edge_types.append(EdgeType(
            name=name,
            description=str(item.get("description") or name),
            source_targets=source_targets,
        ))

    contributions = data.get("key_contributions") or []
    if isinstance(contributions, str):
        contributions = [contributions]
    return Ontology(
        entity_types=entity_types,
        edge_types=edge_types,
        analysis_summary=str(data.get("analysis_summary") or ""),
        paper_domain=str(data.get("paper_domain") or ""),
        key_contributions=[str(x) for x in contributions if str(x).strip()],
    )


def _json_string(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value


def _json_string_list(raw: str) -> list[str]:
    return [_json_string(match.group(1)) for match in re.finditer(r'"((?:\\.|[^"\\])*)"', raw or "")]


def _salvage_truncated_ontology(raw: str) -> Ontology | None:
    """Recover complete ontology records from an otherwise truncated JSON object."""
    cleaned = sanitize_structured_text(raw)
    entity_region = cleaned
    edge_region = cleaned
    edge_pos = cleaned.find('"edge_types"')
    if edge_pos >= 0:
        entity_region = cleaned[:edge_pos]
        edge_region = cleaned[edge_pos:]

    entity_types: list[EntityType] = []
    entity_pattern = re.compile(
        r'\{\s*"name"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*'
        r'"description"\s*:\s*"((?:\\.|[^"\\])*)"'
        r'(?:\s*,\s*"examples"\s*:\s*\[(.*?)\])?',
        re.DOTALL,
    )
    for match in entity_pattern.finditer(entity_region):
        name = _json_string(match.group(1)).strip()
        description = _json_string(match.group(2)).strip()
        if not name:
            continue
        entity_types.append(EntityType(
            name=name,
            description=description or name,
            examples=_json_string_list(match.group(3) or "")[:3],
        ))

    edge_types: list[EdgeType] = []
    edge_pattern = re.compile(
        r'\{\s*"name"\s*:\s*"((?:\\.|[^"\\])*)"\s*,\s*'
        r'"description"\s*:\s*"((?:\\.|[^"\\])*)"',
        re.DOTALL,
    )
    for match in edge_pattern.finditer(edge_region):
        name = _json_string(match.group(1)).strip()
        description = _json_string(match.group(2)).strip()
        if not name:
            continue
        edge_types.append(EdgeType(name=name, description=description or name, source_targets=[]))

    paper_domain = ""
    domain_match = re.search(r'"paper_domain"\s*:\s*"((?:\\.|[^"\\])*)"', cleaned)
    if domain_match:
        paper_domain = _json_string(domain_match.group(1)).strip()

    key_contributions: list[str] = []
    contrib_match = re.search(r'"key_contributions"\s*:\s*\[(.*?)\]', cleaned, re.DOTALL)
    if contrib_match:
        key_contributions = _json_string_list(contrib_match.group(1))[:5]

    if not entity_types and not edge_types and not key_contributions:
        return None
    logger.info(
        "Salvaged %d ontology entity types and %d edge types from truncated JSON",
        len(entity_types), len(edge_types),
    )
    return Ontology(
        entity_types=entity_types,
        edge_types=edge_types,
        paper_domain=paper_domain,
        key_contributions=key_contributions,
    )


def _parse_ontology(raw: str) -> Ontology:
    """Parse LLM output into Ontology.

    Handles model output that wraps JSON in thinking tags, code fences,
    or plain text preamble.
    """
    if not raw or not raw.strip():
        logger.warning("Empty ontology response")
        return Ontology()

    parsed = extract_json_object(
        raw,
        required_keys={"entity_types", "edge_types", "paper_domain"},
        allow_thinking_json=True,
    )
    if parsed is not None:
        return _coerce_ontology_data(parsed)

    cleaned = sanitize_structured_text(raw)

    # Try direct JSON
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return _coerce_ontology_data(data)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    # Try code fence
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", cleaned, re.DOTALL)
    if fence:
        try:
            data = json.loads(fence.group(1))
            if isinstance(data, dict):
                return _coerce_ontology_data(data)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Brace matching for largest valid JSON with ontology keys
    best_data = None
    best_size = 0
    pos = 0
    while pos < len(cleaned):
        start = cleaned.find("{", pos)
        if start < 0:
            break
        depth = 0
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start : i + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict) and len(candidate) > best_size:
                            if "entity_types" in data or "edge_types" in data or "paper_domain" in data:
                                best_data = data
                                best_size = len(candidate)
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
                    pos = i + 1
                    break
        else:
            break

    if best_data:
        return _coerce_ontology_data(best_data)

    salvaged = _salvage_truncated_ontology(raw)
    if salvaged is not None:
        return salvaged

    logger.warning(
        "Failed to parse ontology output (%d chars). First 500: %s",
        len(raw), raw[:500],
    )
    return Ontology()


# ══════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════

async def generate_ontology(
    text: str,
    llm_client: LLMClient,
    model: str = "",
    session_id: str | None = None,
    conference_context: str = "",
    metadata: Any | None = None,
    markdown: str = "",
    domain_config: DomainConfig | None = None,
) -> Ontology:
    """Generate a domain-specific ontology for a document.

    Four-step workflow:
      1. Domain detection + pattern seeding (no LLM)
      2. Self-consistent parallel discovery (N=3 LLM calls)
      3. Grounding verification (1 LLM call)
      4. Merge with base types + validate

    Args:
        text: Full document text.
        llm_client: LLM client for generation calls.
        model: Which model to use.
        session_id: Optional session ID for cost tracking.
        conference_context: Optional venue context (e.g., "HPDC: HPC conference").
        metadata: Optional DocumentMetadata for structural context.
        markdown: Optional structured markdown.

    Returns:
        Ontology with grounded, self-consistent entity and edge types.
    """
    # ── Step 1: Domain Detection ──────────────────────────
    abstract = ""
    sections: list[str] = []
    if metadata:
        abstract = getattr(metadata, "abstract", "") or ""
        sections = getattr(metadata, "sections", []) or []

    domain, seed_types = _detect_domain(abstract, sections, domain_config=domain_config)

    seed_section = ""
    if seed_types:
        seed_section = (
            f"## Seed types for {domain} papers (refine or replace as needed)\n"
            + "\n".join(f"- **{s['name']}**: {s['description']}" for s in seed_types)
            + "\n\n"
        )

    if conference_context:
        seed_section += f"## Conference Context\n{conference_context}\n\n"

    if not model:
        logger.info("No ontology model configured; using deterministic domain seeds")
        return _validate_ontology(_seed_ontology(domain, seed_types), domain_config=domain_config)

    # ── Step 2: Self-Consistent Discovery ──────────────────
    paper_text = _build_focused_text(metadata, markdown, text)
    if not paper_text:
        paper_text = (markdown or text)[:15000]

    discovery_prompt = domain_config.ontology_discovery_prompt if domain_config else ""
    local_or_lan_model = model.startswith(("lan-", "localhost-"))
    draft = await _discover_with_consistency(
        paper_text=paper_text,
        seed_section=seed_section,
        llm_client=llm_client,
        model=model,
        session_id=session_id,
        n_samples=1 if local_or_lan_model else 3,
        discovery_prompt=discovery_prompt,
    )

    if not draft.entity_types and not draft.edge_types:
        logger.warning("Ontology discovery produced no types, using deterministic domain seeds")
        return _validate_ontology(_seed_ontology(domain, seed_types), domain_config=domain_config)

    # ── Step 3: Grounding Verification ─────────────────────
    grounding_prompt = domain_config.ontology_grounding_prompt if domain_config else ""
    grounded = await _ground_ontology(
        ontology=draft,
        paper_text=paper_text,
        llm_client=llm_client,
        model=model,
        session_id=session_id,
        grounding_prompt=grounding_prompt,
    )

    # ── Step 4: Merge + Validate ───────────────────────────
    ontology = _validate_ontology(grounded, domain_config=domain_config)

    if domain_config and domain_config.base_entity_types:
        all_base_e = _seeds_to_entity_types(
            domain_config.base_entity_types + domain_config.fallback_entity_types + domain_config.structural_entity_types
        )
        all_base_r = _seeds_to_edge_types(domain_config.base_edge_types + domain_config.structural_edge_types)
    else:
        all_base_e = _BASE_ENTITY_TYPES + _FALLBACK_ENTITY_TYPES + _STRUCTURAL_ENTITY_TYPES
        all_base_r = _BASE_EDGE_TYPES + _STRUCTURAL_EDGE_TYPES

    base_e_names = {t.name for t in all_base_e}
    base_r_names = {t.name for t in all_base_r}

    llm_types = [et.name for et in ontology.entity_types if et.name not in base_e_names]
    llm_edges = [et.name for et in ontology.edge_types if et.name not in base_r_names]

    logger.info(
        "Ontology complete: domain=%s, %d total entity types (%d document-specific: %s), "
        "%d total edge types (%d document-specific: %s), %d contributions",
        ontology.paper_domain,
        len(ontology.entity_types), len(llm_types), llm_types,
        len(ontology.edge_types), len(llm_edges), llm_edges,
        len(ontology.key_contributions),
    )

    return ontology
