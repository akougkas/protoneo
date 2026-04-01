"""Paper Review application manifest and domain config loader."""

from pathlib import Path

import yaml

from protoneo.config.schema import AppManifest
from protoneo.knowledge.types import DomainConfig, SeedEntity, SeedEdge

_DOMAIN_DIR = Path(__file__).resolve().parent / "domain"


def _load_domain() -> DomainConfig:
    """Load domain configuration from YAML files and prompt templates."""

    # Load seeds
    seeds_path = _DOMAIN_DIR / "seeds.yaml"
    seeds = yaml.safe_load(seeds_path.read_text(encoding="utf-8"))

    base_entity_types = [
        SeedEntity(
            name=e["name"],
            description=e["description"],
            attributes=e.get("attributes", []),
            examples=e.get("examples", []),
        )
        for e in seeds.get("base_entity_types", [])
    ]
    fallback_entity_types = [
        SeedEntity(name=e["name"], description=e["description"],
                   attributes=e.get("attributes", []), examples=e.get("examples", []))
        for e in seeds.get("fallback_entity_types", [])
    ]
    structural_entity_types = [
        SeedEntity(name=e["name"], description=e["description"],
                   attributes=e.get("attributes", []), examples=e.get("examples", []))
        for e in seeds.get("structural_entity_types", [])
    ]
    base_edge_types = [
        SeedEdge(name=e["name"], description=e["description"],
                 source_targets=e.get("source_targets", []))
        for e in seeds.get("base_edge_types", [])
    ]
    structural_edge_types = [
        SeedEdge(name=e["name"], description=e["description"],
                 source_targets=e.get("source_targets", []))
        for e in seeds.get("structural_edge_types", [])
    ]

    # Load domain patterns and keywords
    patterns_path = _DOMAIN_DIR / "domain_patterns.yaml"
    patterns_data = yaml.safe_load(patterns_path.read_text(encoding="utf-8"))
    domain_patterns = patterns_data.get("domain_patterns", {})
    domain_keywords = patterns_data.get("domain_keywords", {})

    # Load config
    config_path = _DOMAIN_DIR / "config.yaml"
    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    # Load prompt templates
    prompts_dir = _DOMAIN_DIR / "prompts"

    def _read_prompt(name: str) -> str:
        p = prompts_dir / name
        return p.read_text(encoding="utf-8") if p.exists() else ""

    return DomainConfig(
        name=config_data.get("name", "academic_paper"),
        base_entity_types=base_entity_types,
        base_edge_types=base_edge_types,
        fallback_entity_types=fallback_entity_types,
        structural_entity_types=structural_entity_types,
        structural_edge_types=structural_edge_types,
        domain_patterns=domain_patterns,
        domain_keywords=domain_keywords,
        ontology_discovery_prompt=_read_prompt("ontology_discovery.md"),
        ontology_grounding_prompt=_read_prompt("ontology_grounding.md"),
        verify_system_prompt=config_data.get("verify_system_prompt", ""),
        verify_connectivity_prompt=_read_prompt("verify_connectivity.md"),
        verify_completeness_prompt=_read_prompt("verify_completeness.md"),
        verify_grounding_prompt=_read_prompt("verify_grounding.md"),
        structural_node_types=set(config_data.get("structural_node_types", ["Document", "Section"])),
        structural_edge_types_for_summary=set(config_data.get("structural_edge_types_for_summary", ["HAS_SECTION", "CONTAINS", "APPEARS_IN"])),
        summary_max_chars=config_data.get("summary_max_chars", 3000),
    )


# Load once at import time
domain_config = _load_domain()

def _on_register(reg):
    """Register paper review exporters into the kernel export registry."""
    from .exporters import ReviewMarkdownExporter, ReviewPdfExporter
    reg.register_exporter(ReviewMarkdownExporter())
    reg.register_exporter(ReviewPdfExporter())


def _get_router():
    from .api import router
    return router


manifest = AppManifest(
    name="paper_review",
    display_name="Paper Review",
    version="0.1.0",
    description="AI peer review panel for academic papers",
    router=_get_router(),
    on_register=_on_register,
    domain_config=domain_config,
    profile_dir=Path(__file__).resolve().parent / "profiles",
    prompt_dir=Path(__file__).resolve().parent / "prompts",
    pipeline_stages=["independent_review", "deliberation", "meta_review", "pc_chair"],
)
