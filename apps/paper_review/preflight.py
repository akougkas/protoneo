"""Preflight checks for manuscript submissions.

Runs fast, heuristic checks before launching the expensive LLM review panel.
Covers page estimation, anonymization, section detection, and venue-fit scoring.
"""

import re
from typing import Any

from pydantic import BaseModel, Field

from protoneo.knowledge.metadata import DocumentMetadata, extract_metadata
from .conference import ConferenceProfile


class PreflightCheck(BaseModel):
    """Result of a single preflight check."""

    name: str
    passed: bool
    detail: str
    severity: str = "info"  # "info", "warning", "blocker"


class PreflightResult(BaseModel):
    """Aggregated preflight results."""

    filename: str
    text_length: int
    estimated_pages: int
    checks: list[PreflightCheck] = Field(default_factory=list)
    pass_count: int = 0
    warn_count: int = 0
    block_count: int = 0
    metadata: DocumentMetadata | None = None

    @property
    def can_proceed(self) -> bool:
        return self.block_count == 0


# Average characters per page for ACM sigconf two-column format.
_CHARS_PER_PAGE = 6000

# Common section headings in academic papers.
_EXPECTED_SECTIONS = {
    "abstract": re.compile(r"(?i)\babstract\b"),
    "introduction": re.compile(r"(?i)\b(?:introduction|intro)\b"),
    "related_work": re.compile(r"(?i)\b(?:related\s+work|background|prior\s+work)\b"),
    "methodology": re.compile(r"(?i)\b(?:method(?:ology|s)?|approach|design|architecture|system\s+(?:design|overview))\b"),
    "evaluation": re.compile(r"(?i)\b(?:evaluation|experiments?|results?|performance)\b"),
    "conclusion": re.compile(r"(?i)\b(?:conclusion|conclusions|summary|concluding\s+remarks)\b"),
    "references": re.compile(r"(?i)\b(?:references|bibliography)\b"),
}

# Patterns that suggest author identity leaks in dual-anonymous submissions.
_IDENTITY_PATTERNS = [
    re.compile(r"(?i)\bour\s+(?:previous|prior|earlier)\s+(?:work|paper|study)\s*\["),
    re.compile(r"(?i)\bas\s+(?:we|the\s+authors?)\s+(?:showed?|demonstrated?|proved?|reported?)\s+in\s*\["),
    re.compile(r"(?i)\bin\s+(?:our|my)\s+(?:previous|prior|earlier)\s+(?:work|paper)\b"),
    re.compile(r"(?i)\b(?:university|institute|lab(?:oratory)?)\s+of\b"),
    re.compile(r"(?i)\b[A-Z][a-z]+\s+et\s+al\.\s*\[.*?\]\s*(?:show|demonstrate|prove|report)", re.DOTALL),
]

# Keywords signaling venue relevance for HPC/distributed computing conferences.
_HPC_KEYWORDS = [
    "parallel", "distributed", "high-performance", "hpc", "gpu", "accelerator",
    "mpi", "cuda", "openmp", "mapreduce", "spark", "cluster", "supercomputer",
    "datacenter", "data center", "cloud computing", "serverless", "container",
    "scheduling", "load balancing", "scalability", "throughput", "latency",
    "memory hierarchy", "cache", "bandwidth", "i/o", "storage", "file system",
    "network", "interconnect", "heterogeneous", "fpga", "deep learning",
    "machine learning", "inference", "training", "benchmark", "profiling",
    "optimization", "resource management", "virtualization", "microservice",
]


def run_preflight(text: str, filename: str, profile: ConferenceProfile) -> PreflightResult:
    """Run all preflight checks on extracted manuscript text."""
    text_lower = text.lower()
    checks: list[PreflightCheck] = []

    # 1. Page estimate
    estimated_pages = max(1, len(text) // _CHARS_PER_PAGE)
    max_pages = profile.max_pages

    if estimated_pages > max_pages + 2:
        checks.append(PreflightCheck(
            name="page_limit",
            passed=False,
            detail=f"Estimated ~{estimated_pages} pages exceeds {max_pages}-page limit by a wide margin",
            severity="warning",
        ))
    elif estimated_pages > max_pages:
        checks.append(PreflightCheck(
            name="page_limit",
            passed=False,
            detail=f"Estimated ~{estimated_pages} pages may exceed {max_pages}-page limit (references excluded from limit)",
            severity="info",
        ))
    else:
        checks.append(PreflightCheck(
            name="page_limit",
            passed=True,
            detail=f"Estimated ~{estimated_pages} pages within {max_pages}-page limit",
        ))

    # 2. Section detection
    found_sections = []
    missing_sections = []
    for section_name, pattern in _EXPECTED_SECTIONS.items():
        if pattern.search(text):
            found_sections.append(section_name)
        else:
            missing_sections.append(section_name)

    if missing_sections:
        missing_str = ", ".join(missing_sections)
        severity = "warning" if "abstract" in missing_sections or "conclusion" in missing_sections else "info"
        checks.append(PreflightCheck(
            name="sections",
            passed=len(missing_sections) <= 2,
            detail=f"Found {len(found_sections)}/{len(_EXPECTED_SECTIONS)} expected sections. Missing: {missing_str}",
            severity=severity,
        ))
    else:
        checks.append(PreflightCheck(
            name="sections",
            passed=True,
            detail=f"All {len(_EXPECTED_SECTIONS)} expected sections detected",
        ))

    # 3. Anonymization check (dual-anonymous venues only)
    if profile.dual_anonymous:
        identity_hits = []
        for pattern in _IDENTITY_PATTERNS:
            match = pattern.search(text)
            if match:
                snippet = match.group(0)[:80].strip()
                identity_hits.append(snippet)

        if identity_hits:
            checks.append(PreflightCheck(
                name="anonymization",
                passed=False,
                detail=f"Found {len(identity_hits)} potential identity leak(s): \"{identity_hits[0]}\"...",
                severity="warning",
            ))
        else:
            checks.append(PreflightCheck(
                name="anonymization",
                passed=True,
                detail="No obvious identity leaks detected (dual-anonymous)",
            ))

    # 4. Venue-fit heuristic
    keyword_hits = sum(1 for kw in _HPC_KEYWORDS if kw in text_lower)
    total_keywords = len(_HPC_KEYWORDS)
    fit_ratio = keyword_hits / total_keywords

    if fit_ratio < 0.05:
        checks.append(PreflightCheck(
            name="venue_fit",
            passed=False,
            detail=f"Only {keyword_hits}/{total_keywords} venue-relevant keywords found. Paper may not fit {profile.short_name or profile.slug}",
            severity="warning",
        ))
    elif fit_ratio < 0.15:
        checks.append(PreflightCheck(
            name="venue_fit",
            passed=True,
            detail=f"{keyword_hits}/{total_keywords} venue-relevant keywords found. Moderate fit for {profile.short_name or profile.slug}",
        ))
    else:
        checks.append(PreflightCheck(
            name="venue_fit",
            passed=True,
            detail=f"{keyword_hits}/{total_keywords} venue-relevant keywords found. Strong fit for {profile.short_name or profile.slug}",
        ))

    # 5. Limitations section
    if re.search(r"(?i)\b(?:limitations?|threats?\s+to\s+validity)\b", text):
        checks.append(PreflightCheck(
            name="limitations",
            passed=True,
            detail="Limitations or threats to validity section detected",
        ))
    else:
        checks.append(PreflightCheck(
            name="limitations",
            passed=False,
            detail="No explicit limitations section detected",
            severity="info",
        ))

    # 6. References count
    ref_matches = re.findall(r"\[\d+\]", text)
    ref_count = len(set(ref_matches))
    if ref_count < 10:
        checks.append(PreflightCheck(
            name="references",
            passed=False,
            detail=f"Only ~{ref_count} unique references detected. Most accepted papers cite 25+",
            severity="info",
        ))
    else:
        checks.append(PreflightCheck(
            name="references",
            passed=True,
            detail=f"~{ref_count} unique references detected",
        ))

    # Tally
    pass_count = sum(1 for c in checks if c.passed)
    warn_count = sum(1 for c in checks if not c.passed and c.severity == "warning")
    block_count = sum(1 for c in checks if not c.passed and c.severity == "blocker")

    metadata = extract_metadata(text)

    return PreflightResult(
        filename=filename,
        text_length=len(text),
        estimated_pages=estimated_pages,
        checks=checks,
        pass_count=pass_count,
        warn_count=warn_count,
        block_count=block_count,
        metadata=metadata,
    )
