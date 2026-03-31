"""Knowledge subsystem types and protocols."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class ParseResult:
    """Output of a single parser."""

    text: str
    markdown: str = ""
    figures_dir: str = ""
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class Parser(Protocol):
    """A document parsing backend. Stateless, registered once at startup."""

    @property
    def name(self) -> str: ...

    @property
    def supported_extensions(self) -> set[str]: ...

    def available(self) -> bool:
        """Check if dependencies are installed (import check, binary check)."""
        ...

    async def parse(self, path: Path, options: dict | None = None) -> ParseResult: ...


@dataclass
class SeedEntity:
    """A domain-specific entity type seed."""

    name: str
    description: str
    attributes: list[dict] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)


@dataclass
class SeedEdge:
    """A domain-specific relationship type seed."""

    name: str
    description: str
    source_targets: list[dict] = field(default_factory=list)


@dataclass
class DomainConfig:
    """Domain expertise injected by an application into kernel knowledge
    modules. The kernel provides the algorithms. The application provides
    the prompts, seeds, and classification rules."""

    name: str

    # Ontology generation
    base_entity_types: list[SeedEntity] = field(default_factory=list)
    base_edge_types: list[SeedEdge] = field(default_factory=list)
    fallback_entity_types: list[SeedEntity] = field(default_factory=list)
    structural_entity_types: list[SeedEntity] = field(default_factory=list)
    structural_edge_types: list[SeedEdge] = field(default_factory=list)
    domain_patterns: dict[str, list[dict]] = field(default_factory=dict)
    domain_keywords: dict[str, list[str]] = field(default_factory=dict)
    ontology_discovery_prompt: str = ""
    ontology_grounding_prompt: str = ""

    # Graph extraction
    extraction_system_prompt: str = ""
    extraction_batch_size: int = 3

    # Verification
    verify_system_prompt: str = ""
    verify_connectivity_prompt: str = ""
    verify_completeness_prompt: str = ""
    verify_grounding_prompt: str = ""

    # Graph presentation
    structural_node_types: set[str] = field(
        default_factory=lambda: {"Document", "Section"}
    )
    structural_edge_types_for_summary: set[str] = field(
        default_factory=lambda: {"HAS_SECTION", "CONTAINS", "APPEARS_IN"}
    )
    summary_max_chars: int = 3000
    summary_template: str = ""
