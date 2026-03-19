"""Conference profile loading and management."""

import yaml
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ReviewScale(BaseModel):
    scale: list[int] = Field(default_factory=lambda: [1, 5])
    labels: dict[int, str] = Field(default_factory=dict)


class ReviewFormConfig(BaseModel):
    overall_merit: ReviewScale = Field(default_factory=ReviewScale)
    reviewer_expertise: ReviewScale = Field(
        default_factory=lambda: ReviewScale(scale=[1, 4])
    )
    sections: list[str] = Field(default_factory=list)


class PanelAgentDef(BaseModel):
    role: str
    focus: list[str] = Field(default_factory=list)


class ConferenceProfile(BaseModel):
    slug: str
    name: str
    short_name: str = ""
    location: str = ""
    dates: str = ""
    paper_types: list[dict[str, str]] = Field(default_factory=list)
    scope_summary: str = ""
    scope_topics: list[str] = Field(default_factory=list)
    must_show_connection: bool = False
    max_pages: int = 11
    format_style: str = "ACM sigconf"
    dual_anonymous: bool = True
    review_form: ReviewFormConfig = Field(default_factory=ReviewFormConfig)
    panel_agents: dict[str, PanelAgentDef] = Field(default_factory=dict)
    optional_agents: dict[str, PanelAgentDef] = Field(default_factory=dict)
    preflight_checks: list[str] = Field(default_factory=list)
    graph_pruning_threshold: float = 0.3

    def scope_text(self) -> str:
        lines = [self.scope_summary]
        if self.scope_topics:
            lines.append("Topics: " + "; ".join(self.scope_topics))
        return "\n".join(lines)

    def merit_labels(self) -> dict[int, str]:
        return self.review_form.overall_merit.labels

    def expertise_labels(self) -> dict[int, str]:
        return self.review_form.reviewer_expertise.labels


def _profiles_dir() -> Path:
    """Profile YAML files live alongside this module."""
    return Path(__file__).resolve().parent / "profiles"


def load_profile(slug: str) -> ConferenceProfile:
    """Load a conference profile by slug."""
    profile_path = _profiles_dir() / f"{slug}.profile.yaml"
    if not profile_path.exists():
        raise FileNotFoundError(f"Conference profile not found: {slug}")

    raw = yaml.safe_load(profile_path.read_text())
    conf = raw.get("conference", {})
    scope = raw.get("scope", {})
    sub = raw.get("submission", {})
    form = raw.get("review_form", {})
    panel = raw.get("panel", {})
    checks = raw.get("preflight_checks", [])

    review_form = ReviewFormConfig(
        overall_merit=ReviewScale(**form.get("overall_merit", {})),
        reviewer_expertise=ReviewScale(**form.get("reviewer_expertise", {})),
        sections=form.get("sections", []),
    )

    agents = {}
    for aid, adef in panel.get("agents", {}).items():
        agents[aid] = PanelAgentDef(
            role=adef.get("role", aid), focus=adef.get("focus", [])
        )

    optional = {}
    for aid, adef in panel.get("optional_agents", {}).items():
        optional[aid] = PanelAgentDef(
            role=adef.get("role", aid), focus=adef.get("focus", [])
        )

    graph_cfg = raw.get("graph", {})

    return ConferenceProfile(
        slug=conf.get("slug", slug),
        name=conf.get("name", slug),
        short_name=conf.get("short_name", ""),
        location=conf.get("location", ""),
        dates=conf.get("dates", ""),
        paper_types=conf.get("paper_types", []),
        scope_summary=scope.get("summary", ""),
        scope_topics=scope.get("topics", []),
        must_show_connection=scope.get("must_show_connection_to_hpdc",
                                      scope.get("must_show_scope_connection", False)),
        max_pages=sub.get("max_pages_excluding_references", 11),
        format_style=sub.get("format", "ACM sigconf"),
        dual_anonymous=sub.get("dual_anonymous", True),
        review_form=review_form,
        panel_agents=agents,
        optional_agents=optional,
        preflight_checks=checks,
        graph_pruning_threshold=graph_cfg.get("pruning_threshold", 0.3),
    )


def list_profiles() -> list[ConferenceProfile]:
    """List all available conference profiles."""
    profiles = []
    profiles_dir = _profiles_dir()
    if not profiles_dir.exists():
        return profiles
    for path in sorted(profiles_dir.glob("*.profile.yaml")):
        slug = path.stem.replace(".profile", "")
        try:
            profiles.append(load_profile(slug))
        except Exception:
            continue
    return profiles
