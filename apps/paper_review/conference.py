"""Conference profile loading and management."""

import yaml
from pathlib import Path
import json
import os
import re
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


def _user_profiles_dir() -> Path:
    """User-generated venue profiles stay outside the repository."""
    configured = os.getenv("PROTONEO_PAPER_REVIEW_PROFILE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".protoneo" / "paper_review" / "profiles"


def _profile_paths(slug: str) -> list[Path]:
    filename = f"{slug}.profile.yaml"
    return [
        _user_profiles_dir() / filename,
        _profiles_dir() / filename,
    ]


def load_profile(slug: str) -> ConferenceProfile:
    """Load a conference profile by slug."""
    profile_path = next((p for p in _profile_paths(slug) if p.exists()), None)
    if not profile_path:
        raise FileNotFoundError(f"Conference profile not found: {slug}")

    raw = yaml.safe_load(profile_path.read_text())
    return profile_from_mapping(raw, fallback_slug=slug)


def profile_from_mapping(raw: dict[str, Any], *, fallback_slug: str = "adaptive") -> ConferenceProfile:
    """Build a ConferenceProfile from the persisted profile mapping."""
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
        slug=conf.get("slug", fallback_slug),
        name=conf.get("name", fallback_slug),
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


def _iter_profile_files() -> list[Path]:
    seen: set[str] = set()
    files: list[Path] = []
    for base in (_user_profiles_dir(), _profiles_dir()):
        if not base.exists():
            continue
        for path in sorted(base.glob("*.profile.yaml")):
            slug = path.stem.replace(".profile", "")
            if slug in seen:
                continue
            seen.add(slug)
            files.append(path)
    return files


def list_profiles() -> list[ConferenceProfile]:
    """List all available conference profiles."""
    profiles = []
    for path in _iter_profile_files():
        slug = path.stem.replace(".profile", "")
        try:
            profiles.append(load_profile(slug))
        except Exception:
            continue
    return profiles


def _slugify(value: str, fallback: str = "custom-venue") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60] or fallback


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    return ""


def _extract_page_limit(text: str) -> int:
    match = re.search(r"(?i)(?:up to|maximum|max|limit of)?\s*(\d{1,2})\s+pages?", text)
    return int(match.group(1)) if match else 11


def _extract_topics(text: str) -> list[str]:
    topics: list[str] = []
    in_topic_block = False
    for line in text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if re.match(r"^#+\s+.*(topic|scope|areas)", stripped, re.I):
            in_topic_block = True
            continue
        if in_topic_block and stripped.startswith("#"):
            break
        if in_topic_block and stripped[:2] in {"- ", "* "}:
            topic = stripped[2:].strip()
            if topic:
                topics.append(topic[:160])
        if len(topics) >= 16:
            break
    return topics


def _default_profile_mapping(
    *,
    slug: str,
    name: str,
    source_text: str,
    source_filename: str = "",
) -> dict[str, Any]:
    topics = _extract_topics(source_text)
    excerpt = re.sub(r"\s+", " ", source_text).strip()[:1200]
    scope_summary = (
        excerpt
        if excerpt
        else "Adaptive peer-review venue generated from a user-provided call for papers or review template."
    )
    format_style = "ACM/IEEE or venue-specified format"
    if re.search(r"\bIEEE\b", source_text, re.I):
        format_style = "IEEE conference format"
    elif re.search(r"\bACM\b", source_text, re.I):
        format_style = "ACM conference format"
    return {
        "conference": {
            "slug": slug,
            "name": name,
            "short_name": name,
            "location": "",
            "dates": "",
            "paper_types": [{"id": "regular", "label": "Regular paper"}],
        },
        "scope": {
            "summary": scope_summary,
            "topics": topics,
            "must_show_scope_connection": True,
        },
        "submission": {
            "max_pages_excluding_references": _extract_page_limit(source_text),
            "format": format_style,
            "dual_anonymous": bool(re.search(r"dual[- ]anonymous|double[- ]blind|anonymous", source_text, re.I)),
        },
        "review_form": {
            "overall_merit": {
                "scale": [1, 5],
                "labels": {
                    1: "Reject",
                    2: "Weak reject",
                    3: "Borderline",
                    4: "Accept",
                    5: "Strong accept",
                },
            },
            "reviewer_expertise": {
                "scale": [1, 4],
                "labels": {
                    1: "Low",
                    2: "Medium",
                    3: "High",
                    4: "Expert",
                },
            },
            "sections": [
                "summary",
                "strengths",
                "weaknesses",
                "questions",
                "revision_actions",
            ],
        },
        "panel": {
            "agents": {
                "technical": {
                    "role": "Technical Soundness Reviewer",
                    "focus": ["methodology", "evidence", "correctness", "experimental support"],
                },
                "novelty": {
                    "role": "Novelty and Positioning Reviewer",
                    "focus": ["originality", "related work", "significance", "venue fit"],
                },
                "clarity": {
                    "role": "Clarity and Presentation Reviewer",
                    "focus": ["writing", "organization", "claims", "reader accessibility"],
                },
                "skeptic": {
                    "role": "Adversarial Skeptic",
                    "focus": ["failure modes", "unsupported claims", "threats to validity"],
                },
                "meta_reviewer": {
                    "role": "Meta-Reviewer",
                    "focus": ["synthesis", "score calibration", "author-facing action plan"],
                },
            },
            "optional_agents": {
                "artifact": {
                    "role": "Artifact and Reproducibility Reviewer",
                    "focus": ["artifact availability", "reproducibility", "open science", "experimental setup"],
                }
            },
        },
        "preflight_checks": [
            "Check that the manuscript states a clear contribution.",
            "Check that evaluation evidence is visible and connected to claims.",
            "Check that the paper explains venue fit using the uploaded venue template.",
        ],
        "graph": {"pruning_threshold": 0.3},
        "generated_from": {
            "source_filename": source_filename,
            "mode": "adaptive_template_import",
        },
    }


def profile_mapping_from_template(text: str, *, filename: str = "") -> dict[str, Any]:
    """Create a reusable profile mapping from a CFP, review form, or profile file."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "conference" in parsed:
            return parsed
    except json.JSONDecodeError:
        pass

    try:
        parsed = yaml.safe_load(text)
        if isinstance(parsed, dict) and "conference" in parsed:
            return parsed
    except yaml.YAMLError:
        pass

    name = _first_heading(text) or Path(filename).stem.replace("_", " ").replace("-", " ").title()
    if not name:
        name = "Custom Venue"
    slug = _slugify(name)
    return _default_profile_mapping(
        slug=slug,
        name=name,
        source_text=text,
        source_filename=filename,
    )


def save_profile_mapping(mapping: dict[str, Any]) -> ConferenceProfile:
    """Persist a generated profile in the user profile directory and return it."""
    profile = profile_from_mapping(mapping, fallback_slug="custom-venue")
    out_dir = _user_profiles_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{profile.slug}.profile.yaml"
    path.write_text(yaml.safe_dump(mapping, sort_keys=False), encoding="utf-8")
    return profile
