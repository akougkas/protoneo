"""Output schemas for PC Panel review packets."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class StrengthItem(BaseModel):
    point: str = ""
    evidence: str = ""
    importance: str = "medium"


class WeaknessItem(BaseModel):
    point: str = ""
    evidence: str = ""
    severity: str = "medium"
    fixability: str = "medium"


class RevisionAction(BaseModel):
    priority: str = "should"
    action: str = ""
    target_section: str = ""
    why_it_matters: str = ""


class ReviewerProvenance(BaseModel):
    """Per-reviewer inference provenance for reproducibility."""

    model_id: str = ""
    temperature: float | None = None
    top_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    prompt_pack_version: str = ""


class IndividualReview(BaseModel):
    reviewer_role: str
    agent_id: str
    model: str = ""
    summary: str = ""
    overall_merit: dict[str, Any] = Field(default_factory=dict)
    expertise: dict[str, Any] = Field(default_factory=dict)
    strengths: list[Any] = Field(default_factory=list)
    weaknesses: list[Any] = Field(default_factory=list)
    questions_for_authors: list[str] = Field(default_factory=list)
    comments_for_authors: str = ""
    internal_committee_concerns: list[str] = Field(default_factory=list)
    confidence: dict[str, Any] = Field(default_factory=dict)
    revision_actions: list[Any] = Field(default_factory=list)
    citations: list[dict] = Field(default_factory=list)
    raw_content: str = ""
    provenance: ReviewerProvenance = Field(default_factory=ReviewerProvenance)


class DeliberationRound(BaseModel):
    round_number: int
    entries: list[dict[str, Any]] = Field(default_factory=list)


class MetaReview(BaseModel):
    panel_summary: str = ""
    score_distribution: dict[str, Any] = Field(default_factory=dict)
    consensus: dict[str, Any] = Field(default_factory=dict)
    agreements: list[str] = Field(default_factory=list)
    disagreements: list[Any] = Field(default_factory=list)
    final_recommendation: dict[str, Any] = Field(default_factory=dict)
    confidence: dict[str, Any] = Field(default_factory=dict)
    decision_risk_notes: list[str] = Field(default_factory=list)
    author_facing_summary: str = ""
    prioritized_revision_plan: list[Any] = Field(default_factory=list)
    submission_readiness: dict[str, str] = Field(default_factory=dict)
    raw_content: str = ""


class ReviewPacket(BaseModel):
    session_id: str
    conference: str
    paper_title: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    reviews: list[IndividualReview] = Field(default_factory=list)
    deliberation: list[DeliberationRound] = Field(default_factory=list)
    meta_review: MetaReview = Field(default_factory=MetaReview)
    pc_chair_review: dict[str, Any] = Field(default_factory=dict)
    duration_seconds: float = 0.0
    total_cost: float = 0.0
    graph_summary: str = ""
    graph_node_count: int = 0
    graph_edge_count: int = 0
    graph_utilization: dict[str, Any] = Field(default_factory=dict)
    provenance_metadata: dict[str, Any] = Field(default_factory=dict)
