"""Export renderers for review packets.

Supports Markdown/PDF output, SC Linklings offline review files, and
durable packet artifacts from ReviewPacket data.
"""

import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import markdown
from weasyprint import HTML

from .schemas import ReviewPacket, format_review_item


def _fmt_list(items: list, ordered: bool = False) -> str:
    """Format a list of items as Markdown, handling both strings and dicts."""
    lines = []
    for i, item in enumerate(items, 1):
        prefix = f"{i}." if ordered else "-"
        if isinstance(item, dict):
            lines.append(f"{prefix} {format_review_item(item)}")
        else:
            lines.append(f"{prefix} {format_review_item(item)}")
    return "\n".join(lines)


_LINKLINGS_REQUIRED_MC = (
    "Relevance",
    "Technical Soundness",
    "Technical Importance",
    "Originality",
    "Quality of Presentation",
    "Recommended Action",
    "Level of confidence in your recommendation",
    "Level of your expertise in the relevant area",
    "Should this submission be considered for the Best Paper award",
)

_RATING_BY_SCORE = {
    1: "VERY LOW",
    2: "LOW",
    3: "MODERATE",
    4: "HIGH",
    5: "VERY HIGH",
}

_RECOMMENDATION_BY_SCORE = {
    1: "STRONG REJECT",
    2: "REJECT",
    4: "ACCEPT",
    5: "STRONG ACCEPT",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _score(value: Any, default: int = 3) -> int:
    if isinstance(value, dict):
        value = value.get("score", default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _label(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("label", "")
    return str(value or "").strip()


def _rating_label(value: Any, default_score: int = 3) -> str:
    label = _label(value).upper()
    if label in {"NONE", "VERY LOW", "LOW", "MODERATE", "HIGH", "VERY HIGH"}:
        return label
    return _RATING_BY_SCORE.get(max(1, min(5, _score(value, default_score))), "MODERATE")


def _expertise_label(final_review: dict[str, Any]) -> str:
    value = final_review.get("level_of_expertise") or final_review.get("reviewer_expertise")
    label = _label(value).upper()
    if label in {"NONE", "VERY LOW", "LOW", "MODERATE", "HIGH", "VERY HIGH"}:
        return label
    score = max(1, min(4, _score(value, 3)))
    return {1: "NONE", 2: "LOW", 3: "HIGH", 4: "VERY HIGH"}.get(score, "HIGH")


def _borderline_lean_accept(final_review: dict[str, Any]) -> bool:
    readiness = _as_dict(final_review.get("submission_readiness"))
    status = str(readiness.get("status", "")).lower()
    haystack = " ".join(
        str(part or "")
        for part in (
            _as_dict(final_review.get("recommended_action")).get("rationale"),
            _as_dict(final_review.get("overall_merit")).get("rationale"),
            final_review.get("comments_for_authors"),
            final_review.get("comments_for_pc"),
            status,
        )
    ).lower()
    negative_markers = (
        "lean reject",
        "weak reject",
        "below the bar",
        "not yet ready",
        "revise before submit",
        "argue against",
    )
    positive_markers = (
        "lean accept",
        "weak accept",
        "above the bar",
        "ready",
        "argue for acceptance",
        "acceptance is warranted",
    )
    if any(marker in haystack for marker in negative_markers):
        return False
    if any(marker in haystack for marker in positive_markers):
        return True
    return len(final_review.get("strengths") or []) >= len(final_review.get("weaknesses") or [])


def _recommended_action_label(final_review: dict[str, Any]) -> str:
    value = final_review.get("recommended_action") or final_review.get("overall_merit")
    label = _label(value).upper()
    allowed = {
        "STRONG REJECT",
        "REJECT",
        "WEAK REJECT",
        "WEAK ACCEPT",
        "ACCEPT",
        "STRONG ACCEPT",
    }
    if label in allowed:
        return label

    score = max(1, min(5, _score(value or final_review.get("overall_merit"), 3)))
    if score == 3:
        return "WEAK ACCEPT" if _borderline_lean_accept(final_review) else "WEAK REJECT"
    return _RECOMMENDATION_BY_SCORE.get(score, "WEAK REJECT")


def _best_paper_label(final_review: dict[str, Any]) -> str:
    bp = _as_dict(final_review.get("best_paper_consideration"))
    if "nominate" in bp:
        return "Yes" if bool(bp.get("nominate")) else "No"
    if _recommended_action_label(final_review) == "STRONG ACCEPT":
        return "Yes"
    return "No"


def _text_list(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(f"- {format_review_item(item)}" for item in value if format_review_item(item))
    return format_review_item(value)


def _questions_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(
            f"{idx}. {format_review_item(item)}"
            for idx, item in enumerate(value, 1)
            if format_review_item(item)
        )
    return format_review_item(value)


def _offline_text_fields(final_review: dict[str, Any]) -> dict[str, str]:
    comments_for_rebuttal = final_review.get("comments_for_rebuttal") or _questions_text(
        final_review.get("questions_for_authors")
    )
    detailed_comments = (
        final_review.get("detailed_comments_for_authors")
        or final_review.get("comments_for_authors")
    )
    comments_for_pc = final_review.get("comments_for_pc") or _text_list(
        final_review.get("internal_committee_concerns")
    )
    return {
        "Summary and High Level Discussion": format_review_item(
            final_review.get("paper_summary")
        ),
        "Strengths": _text_list(final_review.get("strengths")),
        "Weaknesses": _text_list(final_review.get("weaknesses")),
        "Comments for Rebuttal": comments_for_rebuttal,
        "Detailed Comments for Authors": format_review_item(detailed_comments),
        "Reproducibility": format_review_item(
            final_review.get("reproducibility_committee_focus")
        ),
        "Confidential Comments to the Program Committee": format_review_item(comments_for_pc),
    }


def _offline_choice_fields(final_review: dict[str, Any]) -> dict[str, str]:
    return {
        "Relevance": _rating_label(final_review.get("relevance"), 4),
        "Technical Soundness": _rating_label(final_review.get("technical_soundness"), 3),
        "Technical Importance": _rating_label(final_review.get("technical_importance"), 3),
        "Originality": _rating_label(final_review.get("originality"), 3),
        "Quality of Presentation": _rating_label(final_review.get("quality_of_presentation"), 3),
        "Recommended Action": _recommended_action_label(final_review),
        "Level of confidence in your recommendation": _rating_label(
            final_review.get("level_of_confidence") or final_review.get("confidence"),
            4,
        ),
        "Level of your expertise in the relevant area": _expertise_label(final_review),
        "Should this submission be considered for the Best Paper award": _best_paper_label(final_review),
    }


def _find_question_block(lines: list[str], title: str) -> tuple[int, int, int]:
    title_lower = title.lower()
    for start, line in enumerate(lines):
        if not line.lstrip().startswith("<<"):
            continue
        header_parts = [line]
        end = start
        while end < len(lines) and ">>" not in lines[end]:
            end += 1
            if end < len(lines):
                header_parts.append(lines[end])
        header = "\n".join(header_parts).lower()
        if title_lower not in header:
            continue
        next_start = len(lines)
        for idx in range(end + 1, len(lines)):
            if lines[idx].lstrip().startswith("<<"):
                next_start = idx
                break
        return start, end, next_start
    raise ValueError(f"Offline review template question not found: {title}")


def _fill_text_response(lines: list[str], title: str, text: str) -> None:
    _, end, next_start = _find_question_block(lines, title)
    preserved_comments = [
        line for line in lines[end + 1:next_start]
        if line.strip().startswith("//")
    ]
    body = [line.rstrip() for line in str(text or "").strip().splitlines()]
    replacement = ["", *(body or [""]), "", ""]
    if preserved_comments:
        replacement.extend([*preserved_comments, ""])
    lines[end + 1:next_start] = replacement


def _option_key(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("//"):
        cleaned = cleaned[2:].strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.split("(", 1)[0].strip().upper()


def _option_matches(option_text: str, selected: str) -> bool:
    key = _option_key(option_text)
    target = selected.strip().upper()
    return (
        key == target
        or key.startswith(target + " ")
        or bool(re.match(rf"^{re.escape(target)}(?:\b|[,;:.-])", key))
    )


def _select_multiple_choice(lines: list[str], title: str, selected: str) -> None:
    _, end, next_start = _find_question_block(lines, title)
    matched = False
    for idx in range(end + 1, next_start):
        raw = lines[idx]
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("//"):
            option_text = stripped[2:].strip()
        elif _option_key(stripped):
            option_text = stripped
        else:
            continue
        if "(" not in option_text and title != "Should this submission be considered for the Best Paper award":
            continue
        if _option_matches(option_text, selected):
            lines[idx] = option_text
            matched = True
        else:
            lines[idx] = f"//{option_text}"
    if not matched:
        raise ValueError(f"Offline review option {selected!r} not found for {title!r}")


def fill_linklings_offline_review_template(
    template_text: str,
    final_review: dict[str, Any],
) -> str:
    """Fill an SC Linklings offline-review template without changing prompts.

    The template's `<<...>>` question/comment lines are preserved. Text answers
    are inserted below their question blocks, and multiple-choice answers are
    selected by uncommenting exactly one option line in each required block.
    """
    lines = template_text.splitlines()
    for title, text in _offline_text_fields(final_review).items():
        _fill_text_response(lines, title, text)
    for title, selected in _offline_choice_fields(final_review).items():
        _select_multiple_choice(lines, title, selected)
    trailing = "\n" if template_text.endswith("\n") else ""
    return "\n".join(lines) + trailing


def linklings_selection_counts(template_text: str) -> dict[str, int]:
    """Return selected option counts for required Linklings MC fields."""
    lines = template_text.splitlines()
    counts: dict[str, int] = {}
    for title in _LINKLINGS_REQUIRED_MC:
        try:
            _, end, next_start = _find_question_block(lines, title)
        except ValueError:
            continue
        selected = 0
        for line in lines[end + 1:next_start]:
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue
            if "(" in stripped or title == "Should this submission be considered for the Best Paper award":
                selected += 1
        counts[title] = selected
    return counts


def final_review_to_markdown(final_review: dict[str, Any]) -> str:
    """Render just the final SC review fields as Markdown."""
    choices = _offline_choice_fields(final_review)
    lines = ["# Final Review", ""]
    for title, text in _offline_text_fields(final_review).items():
        if text:
            lines.extend([f"## {title}", "", text, ""])
    lines.extend(["## Offline Review Choices", ""])
    for title, selected in choices.items():
        lines.append(f"- **{title}:** {selected}")
    return "\n".join(lines).strip() + "\n"


def _reviews_to_markdown(packet: ReviewPacket) -> str:
    lines = ["# Independent Reviews", ""]
    for review in packet.reviews:
        lines.extend([f"## {review.reviewer_role}", ""])
        if review.summary:
            lines.extend([review.summary, ""])
        if review.overall_merit:
            lines.append(f"Merit: {review.overall_merit.get('score', '')} {review.overall_merit.get('label', '')}".strip())
            lines.append("")
        if review.strengths:
            lines.extend(["### Strengths", "", _fmt_list(review.strengths), ""])
        if review.weaknesses:
            lines.extend(["### Weaknesses", "", _fmt_list(review.weaknesses), ""])
        if review.questions_for_authors:
            lines.extend(["### Questions for Authors", "", _fmt_list(review.questions_for_authors, ordered=True), ""])
        if review.comments_for_authors:
            lines.extend(["### Comments for Authors", "", review.comments_for_authors, ""])
        if review.internal_committee_concerns:
            lines.extend(["### Internal Committee Concerns", "", _fmt_list(review.internal_committee_concerns), ""])
    return "\n".join(lines).strip() + "\n"


def _deliberation_to_markdown(packet: ReviewPacket) -> str:
    lines = ["# Deliberation Transcript", ""]
    for rnd in packet.deliberation:
        lines.extend([f"## Round {rnd.round_number}", ""])
        for entry in rnd.entries:
            role = entry.get("role") or entry.get("speaker_id") or entry.get("agent_id") or "Reviewer"
            speaker = entry.get("speaker_id") or entry.get("agent_id") or ""
            content = entry.get("content", "")
            lines.extend([f"### {role}" + (f" (`{speaker}`)" if speaker else ""), "", content, ""])
    return "\n".join(lines).strip() + "\n"


def _json_ready(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def write_review_artifacts(
    packet: ReviewPacket,
    output_dir: str | Path,
    *,
    source_graph: Any | None = None,
    template_path: str | Path | None = None,
    paper_id: str = "",
    paper_path: str | Path | None = None,
    source_graph_path: str | Path | None = None,
    source_session_id: str = "",
    model_map: dict[str, str] | None = None,
    preset: str = "",
    prompt_pack_version: str = "",
    artifact_description_assumed_present: bool = False,
) -> dict[str, Any]:
    """Write durable per-paper ProtoNeo review artifacts."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    graph_source = (
        packet.provenance_metadata.get("graph_source", "")
        or ("imported" if source_graph_path else "session")
    )
    graph_name = "imported_graph.json" if graph_source == "imported" else "graph.json"
    graph_payload = _json_ready(source_graph) if source_graph is not None else {}

    artifacts: dict[str, Path] = {}

    def _write_json(name: str, data: Any) -> Path:
        path = out / name
        path.write_text(json.dumps(_json_ready(data), indent=2, ensure_ascii=False) + "\n")
        artifacts[name] = path
        return path

    def _write_text(name: str, data: str) -> Path:
        path = out / name
        path.write_text(data)
        artifacts[name] = path
        return path

    _write_json(graph_name, graph_payload)
    _write_text("graph_summary.md", packet.graph_summary or "")
    web_search_context = packet.provenance_metadata.get("web_search")
    if isinstance(web_search_context, dict):
        _write_json("web_search_context.json", web_search_context)
        if web_search_context.get("markdown"):
            _write_text("web_search_context.md", str(web_search_context["markdown"]))
    _write_json("independent_reviews.json", [r.model_dump(mode="json") for r in packet.reviews])
    _write_text("independent_reviews.md", _reviews_to_markdown(packet))
    _write_json("deliberation_transcript.json", [r.model_dump(mode="json") for r in packet.deliberation])
    _write_text("deliberation_transcript.md", _deliberation_to_markdown(packet))
    _write_json("meta_review.json", packet.meta_review.model_dump(mode="json"))

    final_review = dict(packet.pc_chair_review or {})
    offline_path = None
    if template_path:
        template = Path(template_path)
        filled = fill_linklings_offline_review_template(
            template.read_text(),
            final_review,
        )
        offline_name = f"{paper_id or template.stem.replace('_review', '')}_protoneo_offline_review.txt"
        offline_path = _write_text(offline_name, filled)
        final_review["linklings_offline_review_text"] = filled
        final_review["offline_review_path"] = str(offline_path)

    packet.pc_chair_review = final_review
    _write_json("final_review.json", final_review)
    _write_text("final_review.md", final_review_to_markdown(final_review))

    if not model_map:
        agents = packet.provenance_metadata.get("agents", {})
        model_map = {
            role: info.get("model_id", "")
            for role, info in agents.items()
            if isinstance(info, dict) and info.get("model_id")
        }

    manifest = {
        "session_id": packet.session_id,
        "paper_id": paper_id,
        "paper_path": str(paper_path or ""),
        "conference": packet.conference,
        "model_map": model_map or {},
        "preset": preset,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_graph_path": str(source_graph_path or ""),
        "source_graph_session_id": source_session_id,
        "source_graph_format": graph_source,
        "prompt_pack_version": prompt_pack_version or packet.provenance_metadata.get("prompt_pack_version", ""),
        "artifact_description_assumed_present": artifact_description_assumed_present,
        "deliberation": packet.provenance_metadata.get("deliberation", {}),
        "artifact_paths": {
            key: str(path)
            for key, path in artifacts.items()
        },
        "completed": bool(offline_path and Path(offline_path).exists()),
    }
    manifest_path = _write_json("run_manifest.json", manifest)
    manifest["artifact_paths"]["run_manifest.json"] = str(manifest_path)
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def packet_to_markdown(packet: ReviewPacket) -> str:
    """Render a ReviewPacket as a Markdown document."""
    lines = []

    # Header
    title = packet.paper_title or "Untitled Paper"
    lines.append(f"# Review Packet: {title}")
    lines.append("")
    lines.append(f"**Conference:** {packet.conference.upper()}")
    lines.append(f"**Session:** `{packet.session_id}`")
    lines.append(f"**Duration:** {packet.duration_seconds:.0f}s")
    if packet.total_cost > 0:
        lines.append(f"**Cost:** ${packet.total_cost:.4f}")
    lines.append("")

    # Meta-review summary (top)
    meta = packet.meta_review
    if meta.panel_summary or meta.author_facing_summary:
        lines.append("---")
        lines.append("")
        lines.append("## Panel Summary")
        lines.append("")
        lines.append(meta.panel_summary or meta.author_facing_summary)
        lines.append("")

    if meta.final_recommendation:
        score = meta.final_recommendation.get("score", "")
        label = meta.final_recommendation.get("label", "")
        if score or label:
            lines.append(f"**Final Recommendation:** {score}/5 ({label})")
            lines.append("")

    if meta.consensus:
        level = meta.consensus.get("level", "")
        summary = meta.consensus.get("summary", "")
        if level:
            line = f"**Consensus:** {level}"
            if summary:
                line += f" \u2014 {summary}"
            lines.append(line)
            lines.append("")

    if meta.score_distribution:
        lines.append("**Score Distribution:**")
        lines.append("")
        lines.append("| Reviewer | Score |")
        lines.append("|----------|-------|")
        for reviewer, score in meta.score_distribution.items():
            lines.append(f"| {reviewer} | {score}/5 |")
        lines.append("")

    # Knowledge Graph
    if packet.graph_node_count or packet.graph_summary:
        lines.append(f"**Knowledge Graph:** {packet.graph_node_count} nodes, {packet.graph_edge_count} edges")
        if packet.graph_utilization and packet.graph_utilization.get("overall_ratio") is not None:
            ratio = packet.graph_utilization["overall_ratio"]
            lines.append(f" | Utilization: {ratio:.0%}")
        lines.append("")

    # Individual reviews
    lines.append("---")
    lines.append("")
    lines.append("## Individual Reviews")
    lines.append("")

    for review in packet.reviews:
        lines.append(f"### {review.reviewer_role}")
        if review.model:
            lines.append(f"*Model: `{review.model}`*")
        lines.append("")

        if review.summary:
            lines.append(f"**Summary:** {review.summary}")
            lines.append("")

        # Scores
        score_parts = []
        if review.overall_merit and review.overall_merit.get("score"):
            label = review.overall_merit.get("label", "")
            score_parts.append(f"Merit: {review.overall_merit['score']}/5 ({label})")
        if review.expertise and review.expertise.get("score"):
            label = review.expertise.get("label", "")
            score_parts.append(f"Expertise: {review.expertise['score']}")
        if review.confidence and review.confidence.get("score"):
            score_parts.append(f"Confidence: {review.confidence['score']}")
        if score_parts:
            lines.append(" | ".join(score_parts))
            lines.append("")

        if review.strengths:
            lines.append("**Strengths:**")
            lines.append("")
            lines.append(_fmt_list(review.strengths))
            lines.append("")

        if review.weaknesses:
            lines.append("**Weaknesses:**")
            lines.append("")
            lines.append(_fmt_list(review.weaknesses))
            lines.append("")

        if review.questions_for_authors:
            lines.append("**Questions for Authors:**")
            lines.append("")
            lines.append(_fmt_list(review.questions_for_authors, ordered=True))
            lines.append("")

        if review.comments_for_authors:
            lines.append("**Comments for Authors:**")
            lines.append("")
            lines.append(review.comments_for_authors)
            lines.append("")

        if review.internal_committee_concerns:
            lines.append("**Decision Risk Notes:**")
            lines.append("")
            lines.append(_fmt_list(review.internal_committee_concerns))
            lines.append("")

        if review.revision_actions:
            lines.append("**Revision Actions:**")
            lines.append("")
            lines.append(_fmt_list(review.revision_actions))
            lines.append("")

        if review.citations:
            lines.append("**Citations:**")
            lines.append("")
            for cit in review.citations:
                claim = cit.get("claim", "")
                section = cit.get("section", "")
                page = cit.get("page", "")
                ref = []
                if section:
                    ref.append(section)
                if page:
                    ref.append(f"p.{page}")
                loc = ", ".join(ref)
                lines.append(f"- {claim}" + (f" ({loc})" if loc else ""))
            lines.append("")

        lines.append("")

    # Deliberation log
    if packet.deliberation:
        lines.append("---")
        lines.append("")
        lines.append("## Deliberation Log")
        lines.append("")
        for rnd in packet.deliberation:
            lines.append(f"### Round {rnd.round_number}")
            lines.append("")
            for entry in rnd.entries:
                role = entry.get("role", entry.get("agent_id", "Unknown"))
                content = entry.get("content", "")
                lines.append(f"**{role}:**")
                lines.append("")
                lines.append(content)
                lines.append("")

    # Meta-review details
    if meta.agreements or meta.disagreements or meta.prioritized_revision_plan:
        lines.append("---")
        lines.append("")
        lines.append("## Meta-Review Details")
        lines.append("")

        if meta.agreements:
            lines.append("### Points of Agreement")
            lines.append("")
            lines.append(_fmt_list(meta.agreements))
            lines.append("")

        if meta.disagreements:
            lines.append("### Points of Disagreement")
            lines.append("")
            for d in meta.disagreements:
                if isinstance(d, dict):
                    lines.append(f"- **{d.get('issue', '')}**")
                    if d.get("why_reviewers_disagree"):
                        lines.append(f"  {d['why_reviewers_disagree']}")
                    if d.get("your_resolution"):
                        lines.append(f"  *Resolution: {d['your_resolution']}*")
                else:
                    lines.append(f"- {d}")
            lines.append("")

        if meta.decision_risk_notes:
            lines.append("### Decision Risk Notes")
            lines.append("")
            lines.append(_fmt_list(meta.decision_risk_notes))
            lines.append("")

        if meta.prioritized_revision_plan:
            lines.append("### Prioritized Revision Plan")
            lines.append("")
            lines.append(_fmt_list(meta.prioritized_revision_plan))
            lines.append("")

        if meta.submission_readiness and meta.submission_readiness.get("status"):
            status = meta.submission_readiness["status"].replace("_", " ").title()
            reason = meta.submission_readiness.get("reason", "")
            lines.append(f"### Submission Readiness: {status}")
            if reason:
                lines.append("")
                lines.append(reason)
            lines.append("")

    # Final Review
    if packet.pc_chair_review:
        lines.append("---")
        lines.append("")
        lines.append("## Final Review")
        lines.append("")

        chair = packet.pc_chair_review
        if chair.get("overall_merit"):
            merit = chair["overall_merit"] if isinstance(chair["overall_merit"], dict) else {}
            score = merit.get("score", "")
            label = merit.get("label", "")
            if score or label:
                lines.append(f"**Overall Merit:** {score}/5 ({label})")
                lines.append("")

        if chair.get("reviewer_expertise"):
            exp = chair["reviewer_expertise"] if isinstance(chair["reviewer_expertise"], dict) else {}
            score = exp.get("score", "")
            label = exp.get("label", "")
            if score or label:
                lines.append(f"**Expertise:** {score} ({label})")
                lines.append("")

        if chair.get("paper_summary"):
            lines.append("**Paper Summary:**")
            lines.append("")
            lines.append(chair["paper_summary"])
            lines.append("")

        if chair.get("strengths"):
            lines.append("**Strengths:**")
            lines.append("")
            val = chair["strengths"]
            if isinstance(val, list):
                lines.append(_fmt_list(val))
            else:
                lines.append(str(val))
            lines.append("")

        if chair.get("weaknesses"):
            lines.append("**Weaknesses:**")
            lines.append("")
            val = chair["weaknesses"]
            if isinstance(val, list):
                lines.append(_fmt_list(val))
            else:
                lines.append(str(val))
            lines.append("")

        if chair.get("comments_for_authors"):
            lines.append("**Comments for Authors:**")
            lines.append("")
            lines.append(str(chair["comments_for_authors"]))
            lines.append("")

        if chair.get("questions_for_authors"):
            lines.append("**Questions for Authors:**")
            lines.append("")
            val = chair["questions_for_authors"]
            if isinstance(val, list):
                lines.append(_fmt_list(val, ordered=True))
            else:
                lines.append(str(val))
            lines.append("")

        if chair.get("revision_actions"):
            lines.append("**Revision Actions:**")
            lines.append("")
            lines.append(_fmt_list(chair["revision_actions"]))
            lines.append("")

        if chair.get("submission_readiness"):
            sr = chair["submission_readiness"]
            status = sr.get("status", "").replace("_", " ").title()
            reason = sr.get("reason", "")
            lines.append(f"**Submission Readiness:** {status}")
            if reason:
                lines.append(f"  {reason}")
            lines.append("")

        if chair.get("comments_for_pc"):
            lines.append("**Comments for PC (internal):**")
            lines.append("")
            lines.append(str(chair["comments_for_pc"]))
            lines.append("")

    # System Provenance block for reproducibility
    if packet.provenance_metadata:
        prov = packet.provenance_metadata
        lines.append("---")
        lines.append("")
        lines.append("## System Provenance")
        lines.append("")
        if prov.get("prompt_pack_version"):
            lines.append(f"**Prompt Pack Version:** {prov['prompt_pack_version']}")
        if prov.get("conference_slug"):
            lines.append(f"**Conference Profile:** {prov['conference_slug']}")
        if prov.get("graph_pruning_threshold") is not None:
            lines.append(f"**Graph Pruning Threshold:** {prov['graph_pruning_threshold']}")
        lines.append("")

        agents_prov = prov.get("agents", {})
        if agents_prov:
            lines.append("| Role | Model | Temperature | Top-P | Top-K | Min-P | Repeat |")
            lines.append("|------|-------|-------------|-------|-------|-------|--------|")
            for role_id, info in agents_prov.items():
                model = info.get("model_id", "")
                temp = info.get("temperature")
                tp = info.get("top_p")
                tk = info.get("top_k")
                mp = info.get("min_p")
                rp = info.get("repeat_penalty")
                lines.append(
                    f"| {role_id} | `{model}` | "
                    f"{temp if temp is not None else '\u2014'} | "
                    f"{tp if tp is not None else '\u2014'} | "
                    f"{tk if tk is not None else '\u2014'} | "
                    f"{mp if mp is not None else '\u2014'} | "
                    f"{rp if rp is not None else '\u2014'} |"
                )
            lines.append("")

    return "\n".join(lines)



_PDF_CSS = """\
@page { size: A4; margin: 2cm; }
body {
    font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.5;
    color: #222;
}
h1 { font-size: 18pt; border-bottom: 2px solid #000; padding-bottom: 6px; margin-bottom: 12px; }
h2 { font-size: 14pt; border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-top: 24px; }
h3 { font-size: 12pt; margin-top: 18px; }
code { font-family: monospace; font-size: 10pt; background: #f4f4f4; padding: 1px 4px; border-radius: 3px; }
hr { border: none; border-top: 1px solid #ddd; margin: 16px 0; }
strong { font-weight: 600; }
em { font-style: italic; color: #555; }
ul, ol { padding-left: 20px; }
li { margin-bottom: 4px; }
"""


def packet_to_pdf(packet: ReviewPacket) -> bytes:
    """Render a ReviewPacket as a PDF document."""
    md_text = packet_to_markdown(packet)
    html_body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])
    full_html = f"<html><head><style>{_PDF_CSS}</style></head><body>{html_body}</body></html>"

    pdf_bytes = HTML(string=full_html).write_pdf()
    return pdf_bytes
