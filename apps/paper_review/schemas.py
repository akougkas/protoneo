"""Output schemas for Paper Review review packets."""

import re
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
    top_k: int | None = None
    min_p: float | None = None
    repeat_penalty: float | None = None
    reasoning_effort: str | None = None
    phase_policy: str | None = None
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


_DEFAULT_FINAL_REVIEW: dict[str, Any] = {
    "reviewer_role": "Meta-Reviewer",
    "panel_summary": "",
    "score_distribution": {},
    "consensus": {},
    "agreements": [],
    "disagreements": [],
    "final_recommendation": {"score": 3, "label": "Borderline"},
    "confidence": {"score": 3, "reason": ""},
    "decision_risk_notes": [],
    "author_facing_summary": "",
    "prioritized_revision_plan": [],
    "overall_merit": {"score": 3, "label": "Borderline"},
    "reviewer_expertise": {"score": 3, "label": "Knowledgeable"},
    "paper_summary": "",
    "strengths": [],
    "weaknesses": [],
    "comments_for_rebuttal": "",
    "detailed_comments_for_authors": "",
    "comments_for_authors": "",
    "comments_for_pc": "",
    "internal_committee_concerns": [],
    "questions_for_authors": [],
    "revision_actions": [],
    "relevance": {"score": 4, "label": "HIGH", "rationale": ""},
    "technical_soundness": {"score": 3, "label": "MODERATE", "rationale": ""},
    "technical_importance": {"score": 3, "label": "MODERATE", "rationale": ""},
    "originality": {"score": 3, "label": "MODERATE", "rationale": ""},
    "quality_of_presentation": {"score": 3, "label": "MODERATE", "rationale": ""},
    "recommended_action": {"score": 3, "label": "WEAK REJECT", "rationale": ""},
    "level_of_confidence": {"score": 4, "label": "HIGH", "reason": ""},
    "level_of_expertise": {"score": 4, "label": "HIGH", "reason": ""},
    "best_paper_consideration": {
        "nominate": False,
        "label": "No",
        "rationale": "",
    },
    "reproducibility_committee_focus": "",
    "linklings_offline_review_text": "",
    "offline_review_path": "",
    "submission_readiness": {
        "status": "revise_before_submit",
        "reason": "",
    },
}


_AUTHOR_TEXT_FIELDS = {
    "paper_summary",
    "comments_for_authors",
    "comments_for_rebuttal",
    "detailed_comments_for_authors",
    "author_facing_summary",
}

_BANNED_AUTHOR_PHRASES = {
    "Yet it lacks": "The paper should add",
    "Major revisions are needed": "The most important revisions are",
    "lacks solid evidence for its key claims": "needs clearer manuscript evidence for its main claims",
    "limiting its relevance": "which weakens the venue fit",
}

_PSEUDO_TAG_RE = re.compile(
    r"\[(?P<body>[^\]]*\b(?:severity|importance|priority|fixability)\s*:[^\]]*)\]",
    re.IGNORECASE,
)
_DETAIL_RE = re.compile(
    r"(?:\s+[—–-]\s+|\s+)(?P<label>Evidence|Target|Why it matters|Expected impact|Resolution|Why reviewers disagree):\s*",
    re.IGNORECASE,
)


def _clean_public_text(value: Any) -> str:
    """Normalize public review prose without changing its substantive meaning."""
    text = _coerce_text(value).strip()
    if not text:
        return ""
    text = text.replace("—", "-").replace("–", "-")
    for phrase, replacement in _BANNED_AUTHOR_PHRASES.items():
        text = re.sub(re.escape(phrase), replacement, text, flags=re.IGNORECASE)
    return re.sub(r"[ \t]+", " ", text).strip()


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        return format_review_item(value)
    return str(value)


def _coerce_score_dict(value: Any, default: dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, (int, float)):
        return {"score": value, "label": default.get("label", "")}
    if isinstance(value, str) and value:
        return {"score": default.get("score", 3), "label": value}
    return dict(default)


def _score_value(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("score")
    try:
        score = int(value)
    except (TypeError, ValueError):
        return None
    return score if 1 <= score <= 5 else None


def _coerce_score_distribution(value: Any) -> dict[str, int]:
    """Return a meaningful reviewer-id/role -> score map.

    Numeric-only keys like {"1": 2, "2": 2} are ambiguous because they can be
    buckets, reviewer placeholders, or hallucinated labels. Drop them unless a
    caller has already replaced them with actual reviewer IDs or role names.
    """
    if not isinstance(value, dict):
        return {}
    cleaned: dict[str, int] = {}
    for key, raw_score in value.items():
        label = str(key).strip()
        if not label or label.isdigit():
            continue
        score = _score_value(raw_score)
        if score is not None:
            cleaned[label] = score
    return cleaned


def _coerce_dimension_dict(value: Any, default: dict[str, Any]) -> dict[str, Any]:
    """Coerce Linklings-style rating fields while preserving rationale text."""
    if isinstance(value, dict):
        result = dict(default)
        result.update(value)
        if "label" in result:
            result["label"] = _coerce_text(result.get("label")).strip()
        if "rationale" in result:
            result["rationale"] = _coerce_text(result.get("rationale")).strip()
        if "reason" in result:
            result["reason"] = _coerce_text(result.get("reason")).strip()
        return result
    if isinstance(value, (int, float)):
        result = dict(default)
        result["score"] = int(value)
        return result
    if isinstance(value, str) and value:
        result = dict(default)
        result["label"] = value.strip()
        return result
    return dict(default)


def _coerce_best_paper(value: Any) -> dict[str, Any]:
    default = dict(_DEFAULT_FINAL_REVIEW["best_paper_consideration"])
    if isinstance(value, dict):
        result = dict(default)
        result.update(value)
        raw_nominate = result.get("nominate", result.get("selected", result.get("yes")))
        if isinstance(raw_nominate, str):
            result["nominate"] = raw_nominate.strip().lower() in {
                "yes",
                "true",
                "1",
                "nominate",
            }
        else:
            result["nominate"] = bool(raw_nominate)
        result["label"] = _coerce_text(result.get("label") or ("Yes" if result["nominate"] else "No"))
        result["rationale"] = _coerce_text(result.get("rationale", ""))
        return result
    if isinstance(value, bool):
        return {
            **default,
            "nominate": value,
            "label": "Yes" if value else "No",
        }
    if isinstance(value, str) and value:
        lowered = value.strip().lower()
        nominate = lowered.startswith("yes") or lowered in {"true", "nominate"}
        return {
            **default,
            "nominate": nominate,
            "label": "Yes" if nominate else "No",
            "rationale": value.strip(),
        }
    return default


def _coerce_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _parse_annotated_item_text(value: str) -> tuple[str, dict[str, str]]:
    """Split legacy pseudo-markdown item strings into primary text and fields."""
    text = _clean_public_text(value)
    fields: dict[str, str] = {}

    def _tag_sub(match: re.Match[str]) -> str:
        for part in match.group("body").split(";"):
            if ":" not in part:
                continue
            key, raw = part.split(":", 1)
            key = key.strip().lower().replace(" ", "_")
            if key in {"severity", "importance", "priority", "fixability"}:
                fields[key] = raw.strip()
        return ""

    text = _PSEUDO_TAG_RE.sub(_tag_sub, text).strip()
    parts = _DETAIL_RE.split(text)
    if len(parts) > 1:
        primary = parts[0].strip(" -")
        for i in range(1, len(parts), 2):
            label = parts[i].strip().lower()
            raw = parts[i + 1].strip(" -") if i + 1 < len(parts) else ""
            next_detail = _DETAIL_RE.search(raw)
            if next_detail:
                raw = raw[: next_detail.start()].strip(" -")
            key = {
                "evidence": "evidence",
                "target": "target_section",
                "why it matters": "why_it_matters",
                "expected impact": "expected_review_impact",
                "resolution": "your_resolution",
                "why reviewers disagree": "why_reviewers_disagree",
            }.get(label)
            if key and raw:
                fields[key] = raw
        text = primary
    return text.strip(), fields


def _coerce_structured_items(value: Any, kind: str) -> list[dict[str, Any]]:
    """Coerce final-review list fields while preserving structured objects."""
    if value is None:
        return []

    items: list[Any]
    if isinstance(value, str):
        items = _split_review_lines(value) or [value]
    else:
        items = _coerce_list(value)

    result: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            obj = dict(item)
        else:
            primary, fields = _parse_annotated_item_text(_coerce_text(item))
            obj = dict(fields)
            if kind == "revision":
                obj["action"] = primary
            else:
                obj["point"] = primary

        if kind == "strength":
            normalized = {
                "point": _clean_public_text(obj.get("point") or obj.get("text") or obj.get("summary")),
                "evidence": _clean_public_text(obj.get("evidence")),
                "importance": _clean_public_text(obj.get("importance") or "medium"),
            }
        elif kind == "weakness":
            normalized = {
                "point": _clean_public_text(obj.get("point") or obj.get("text") or obj.get("summary")),
                "evidence": _clean_public_text(obj.get("evidence")),
                "severity": _clean_public_text(obj.get("severity") or "medium"),
                "fixability": _clean_public_text(obj.get("fixability") or "medium"),
            }
        else:
            normalized = {
                "priority": _clean_public_text(obj.get("priority") or "should"),
                "action": _clean_public_text(obj.get("action") or obj.get("point") or obj.get("text")),
                "target_section": _clean_public_text(obj.get("target_section")),
                "why_it_matters": _clean_public_text(obj.get("why_it_matters") or obj.get("why")),
            }

        if kind in {"strength", "weakness"} and not (
            normalized.get("point") or normalized.get("evidence")
        ):
            continue
        if kind == "revision" and not normalized.get("action"):
            continue
        if any(v for v in normalized.values()):
            result.append(normalized)
    return result


def format_review_item(value: Any) -> str:
    """Render a structured review item as readable author/PC text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "; ".join(
            text for text in (format_review_item(item) for item in value) if text
        )
    if not isinstance(value, dict):
        return str(value)

    primary_keys = (
        "point",
        "action",
        "question",
        "concern",
        "issue",
        "claim",
        "text",
        "description",
        "summary",
        "recommendation",
    )
    primary = next(
        (
            _coerce_text(value.get(key)).strip()
            for key in primary_keys
            if value.get(key)
        ),
        "",
    )

    tags = []
    for key in ("severity", "importance", "priority", "fixability"):
        if value.get(key):
            label = key.replace("_", " ")
            tags.append(f"{label}: {_coerce_text(value[key]).strip()}")

    details = []
    detail_labels = {
        "evidence": "Evidence",
        "target_section": "Target",
        "why_it_matters": "Why it matters",
        "expected_review_impact": "Expected impact",
        "your_resolution": "Resolution",
        "why_reviewers_disagree": "Why reviewers disagree",
    }
    for key, label in detail_labels.items():
        if value.get(key):
            details.append(f"{label}: {_coerce_text(value[key]).strip()}")

    if not primary:
        ignored = set(primary_keys) | set(detail_labels) | {
            "severity",
            "importance",
            "priority",
            "fixability",
        }
        primary = "; ".join(
            f"{key.replace('_', ' ')}: {_coerce_text(val).strip()}"
            for key, val in value.items()
            if key not in ignored and _coerce_text(val).strip()
        )

    text = primary or "; ".join(details)
    if tags:
        text = f"{text} ({'; '.join(tags)})" if text else f"({'; '.join(tags)})"
    if details and primary:
        text = f"{text} - {' '.join(details)}"
    return text.strip()


def _split_review_lines(value: str) -> list[str]:
    lines = []
    for line in value.splitlines():
        cleaned = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def _coerce_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _split_review_lines(value) or ([value.strip()] if value.strip() else [])
    texts = [format_review_item(item) for item in _coerce_list(value)]
    return [text for text in texts if text]


def _coerce_string_map(value: Any, default: dict[str, Any]) -> dict[str, str]:
    if isinstance(value, dict):
        return {str(k): _coerce_text(v) for k, v in value.items()}
    if isinstance(value, str) and value:
        return {"status": value, "reason": ""}
    return {str(k): _coerce_text(v) for k, v in default.items()}


def normalize_final_review_payload(payload: Any) -> dict[str, Any]:
    """Normalize known final-review aliases into the canonical packet shape."""
    if not isinstance(payload, dict):
        payload = {}

    source = payload.get("final_review")
    if not isinstance(source, dict):
        source = payload

    normalized: dict[str, Any] = {
        "overall_merit": (
            source.get("overall_merit")
            or source.get("final_recommendation")
            or payload.get("final_recommendation")
        ),
        "reviewer_role": source.get("reviewer_role") or payload.get("reviewer_role"),
        "panel_summary": source.get("panel_summary") or payload.get("panel_summary"),
        "score_distribution": (
            source.get("score_distribution")
            or payload.get("score_distribution")
        ),
        "consensus": source.get("consensus") or payload.get("consensus"),
        "agreements": source.get("agreements") or payload.get("agreements"),
        "disagreements": source.get("disagreements") or payload.get("disagreements"),
        "final_recommendation": (
            source.get("final_recommendation")
            or payload.get("final_recommendation")
            or source.get("overall_merit")
        ),
        "confidence": source.get("confidence") or payload.get("confidence"),
        "decision_risk_notes": (
            source.get("decision_risk_notes")
            or payload.get("decision_risk_notes")
        ),
        "author_facing_summary": (
            source.get("author_facing_summary")
            or payload.get("author_facing_summary")
        ),
        "prioritized_revision_plan": (
            source.get("prioritized_revision_plan")
            or payload.get("prioritized_revision_plan")
        ),
        "reviewer_expertise": (
            source.get("reviewer_expertise")
            or source.get("expertise")
            or payload.get("expertise")
        ),
        "paper_summary": (
            source.get("paper_summary")
            or source.get("summary")
            or payload.get("author_facing_summary")
            or payload.get("panel_summary")
        ),
        "strengths": source.get("strengths"),
        "weaknesses": source.get("weaknesses"),
        "comments_for_authors": (
            source.get("comments_for_authors")
            or payload.get("author_facing_summary")
            or payload.get("panel_summary")
        ),
        "comments_for_rebuttal": (
            source.get("comments_for_rebuttal")
            or source.get("rebuttal_comments")
            or source.get("questions_for_rebuttal")
        ),
        "detailed_comments_for_authors": (
            source.get("detailed_comments_for_authors")
            or source.get("detailed_author_comments")
        ),
        "comments_for_pc": source.get("comments_for_pc"),
        "internal_committee_concerns": (
            source.get("internal_committee_concerns")
            or payload.get("decision_risk_notes")
        ),
        "questions_for_authors": source.get("questions_for_authors"),
        "revision_actions": (
            source.get("revision_actions")
            or source.get("prioritized_revision_plan")
            or payload.get("prioritized_revision_plan")
        ),
        "submission_readiness": (
            source.get("submission_readiness")
            or payload.get("submission_readiness")
        ),
        "relevance": source.get("relevance") or source.get("venue_relevance"),
        "technical_soundness": (
            source.get("technical_soundness")
            or source.get("soundness")
        ),
        "technical_importance": (
            source.get("technical_importance")
            or source.get("importance")
        ),
        "originality": source.get("originality") or source.get("novelty"),
        "quality_of_presentation": (
            source.get("quality_of_presentation")
            or source.get("presentation_quality")
            or source.get("presentation")
        ),
        "recommended_action": (
            source.get("recommended_action")
            or source.get("recommendation")
            or source.get("overall_merit")
            or payload.get("final_recommendation")
        ),
        "level_of_confidence": (
            source.get("level_of_confidence")
            or source.get("confidence")
            or payload.get("confidence")
        ),
        "level_of_expertise": (
            source.get("level_of_expertise")
            or source.get("reviewer_expertise")
            or source.get("expertise")
            or payload.get("expertise")
        ),
        "best_paper_consideration": (
            source.get("best_paper_consideration")
            or source.get("best_paper")
            or source.get("best_paper_nomination")
        ),
        "reproducibility_committee_focus": (
            source.get("reproducibility_committee_focus")
            or source.get("reproducibility_focus")
        ),
        "linklings_offline_review_text": (
            source.get("linklings_offline_review_text")
            or source.get("offline_review_text")
        ),
        "offline_review_path": source.get("offline_review_path"),
    }

    for key, value in source.items():
        normalized.setdefault(key, value)
    return normalized


def sanitize_final_review(payload: Any, fallback_comments: str = "") -> dict[str, Any]:
    """Coerce final-review data into export-safe canonical fields.

    `ReviewPacket.pc_chair_review` remains a compatibility dict, so this helper
    keeps unknown fields while hardening the fields consumed by exporters.
    """
    source = normalize_final_review_payload(payload)
    defaults = _DEFAULT_FINAL_REVIEW

    sanitized: dict[str, Any] = {
        "reviewer_role": _coerce_text(
            source.get("reviewer_role") or defaults["reviewer_role"]
        ),
        "panel_summary": _clean_public_text(
            source.get("panel_summary", defaults["panel_summary"])
        ),
        "score_distribution": _coerce_score_distribution(
            source.get("score_distribution")
        ),
        "consensus": (
            dict(source.get("consensus"))
            if isinstance(source.get("consensus"), dict)
            else dict(defaults["consensus"])
        ),
        "agreements": _coerce_text_list(source.get("agreements")),
        "disagreements": _coerce_list(source.get("disagreements"))
        if source.get("disagreements") is not None
        else [],
        "final_recommendation": _coerce_score_dict(
            source.get("final_recommendation") or source.get("overall_merit"),
            defaults["final_recommendation"],
        ),
        "confidence": _coerce_dimension_dict(
            source.get("confidence"),
            defaults["confidence"],
        ),
        "decision_risk_notes": _coerce_text_list(
            source.get("decision_risk_notes")
        ),
        "author_facing_summary": _clean_public_text(
            source.get("author_facing_summary", defaults["author_facing_summary"])
        ),
        "prioritized_revision_plan": _coerce_structured_items(
            source.get("prioritized_revision_plan"),
            "revision",
        ),
        "overall_merit": _coerce_score_dict(
            source.get("overall_merit"),
            defaults["overall_merit"],
        ),
        "reviewer_expertise": _coerce_score_dict(
            source.get("reviewer_expertise"),
            defaults["reviewer_expertise"],
        ),
        "paper_summary": _clean_public_text(
            source.get("paper_summary", defaults["paper_summary"])
        ),
        "strengths": _coerce_structured_items(source.get("strengths"), "strength"),
        "weaknesses": _coerce_structured_items(source.get("weaknesses"), "weakness"),
        "comments_for_authors": _clean_public_text(
            source.get("comments_for_authors") or fallback_comments
        ),
        "comments_for_rebuttal": _clean_public_text(
            source.get("comments_for_rebuttal")
        ),
        "detailed_comments_for_authors": _clean_public_text(
            source.get("detailed_comments_for_authors")
        ),
        "comments_for_pc": _coerce_text(source.get("comments_for_pc")),
        "internal_committee_concerns": _coerce_text_list(
            source.get("internal_committee_concerns")
        ),
        "questions_for_authors": _coerce_text_list(
            source.get("questions_for_authors")
        ),
        "revision_actions": _coerce_structured_items(
            source.get("revision_actions"),
            "revision",
        ),
        "submission_readiness": _coerce_string_map(
            source.get("submission_readiness"),
            defaults["submission_readiness"],
        ),
        "relevance": _coerce_dimension_dict(
            source.get("relevance"),
            defaults["relevance"],
        ),
        "technical_soundness": _coerce_dimension_dict(
            source.get("technical_soundness"),
            defaults["technical_soundness"],
        ),
        "technical_importance": _coerce_dimension_dict(
            source.get("technical_importance"),
            defaults["technical_importance"],
        ),
        "originality": _coerce_dimension_dict(
            source.get("originality"),
            defaults["originality"],
        ),
        "quality_of_presentation": _coerce_dimension_dict(
            source.get("quality_of_presentation"),
            defaults["quality_of_presentation"],
        ),
        "recommended_action": _coerce_dimension_dict(
            source.get("recommended_action"),
            defaults["recommended_action"],
        ),
        "level_of_confidence": _coerce_dimension_dict(
            source.get("level_of_confidence"),
            defaults["level_of_confidence"],
        ),
        "level_of_expertise": _coerce_dimension_dict(
            source.get("level_of_expertise"),
            defaults["level_of_expertise"],
        ),
        "best_paper_consideration": _coerce_best_paper(
            source.get("best_paper_consideration")
        ),
        "reproducibility_committee_focus": _coerce_text(
            source.get("reproducibility_committee_focus")
        ),
        "linklings_offline_review_text": _coerce_text(
            source.get("linklings_offline_review_text")
        ),
        "offline_review_path": _coerce_text(source.get("offline_review_path")),
    }

    if not source.get("overall_merit") and source.get("final_recommendation"):
        sanitized["overall_merit"] = dict(sanitized["final_recommendation"])
    if not source.get("final_recommendation") and source.get("overall_merit"):
        sanitized["final_recommendation"] = dict(sanitized["overall_merit"])

    for key, value in source.items():
        if key in _AUTHOR_TEXT_FIELDS:
            sanitized.setdefault(key, _clean_public_text(value))
        else:
            sanitized.setdefault(key, value)
    return sanitized
