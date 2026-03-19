"""Export renderers for review packets.

Supports Markdown and PDF output from ReviewPacket data.
"""

import io

import markdown
from weasyprint import HTML

from .schemas import ReviewPacket


def _fmt_list(items: list, ordered: bool = False) -> str:
    """Format a list of items as Markdown, handling both strings and dicts."""
    lines = []
    for i, item in enumerate(items, 1):
        prefix = f"{i}." if ordered else "-"
        if isinstance(item, dict):
            point = item.get("point", item.get("action", str(item)))
            evidence = item.get("evidence", "")
            severity = item.get("severity", item.get("importance", item.get("priority", "")))
            line = f"{prefix} **{point}**"
            if severity:
                line += f" [{severity}]"
            if evidence:
                line += f"\n   _{evidence}_"
            lines.append(line)
        else:
            lines.append(f"{prefix} {item}")
    return "\n".join(lines)


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
