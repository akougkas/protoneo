#!/usr/bin/env python3
"""Build a multi-paper quality comparison report from completed sessions."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


HIDDEN_REASONING_RE = re.compile(
    r"<(?:think|thinking)\b|</(?:think|thinking)>|chain[-_\s]*of[-_\s]*thought|"
    r"^\s*(?:reasoning|analysis|scratchpad)\s*:",
    re.IGNORECASE | re.MULTILINE,
)


def _json_object_from_text(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object from an LLM output string."""
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _metadata(session: dict[str, Any]) -> dict[str, Any]:
    config = session.get("config") if isinstance(session.get("config"), dict) else {}
    nested = config.get("metadata")
    if isinstance(nested, dict):
        merged = dict(config)
        merged.update(nested)
        return merged
    return config


def _score(value: Any) -> int | None:
    if isinstance(value, dict):
        value = value.get("score")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        match = re.search(r"\d+", value)
        if match:
            return int(match.group())
    return None


def _list_len(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if value:
        return 1
    return 0


def _phase_outputs(result: dict[str, Any], phase_name: str) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for phase in result.get("phases", []):
        if not isinstance(phase, dict) or phase.get("phase_name") != phase_name:
            continue
        for output in phase.get("outputs", []):
            if isinstance(output, dict):
                outputs.append(output)
    return outputs


def _text_fields(session: dict[str, Any]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    result = session.get("result") if isinstance(session.get("result"), dict) else {}
    for phase in result.get("phases", []):
        if not isinstance(phase, dict):
            continue
        phase_name = phase.get("phase_name", "unknown")
        for output in phase.get("outputs", []):
            if not isinstance(output, dict):
                continue
            content = output.get("content")
            if isinstance(content, str):
                label = f"{phase_name}:{output.get('agent_id', 'unknown')}"
                fields.append((label, content))
    for key in ("final_review", "pc_chair_review"):
        value = result.get(key)
        if value:
            fields.append((key, json.dumps(value, sort_keys=True)))
    return fields


def _updated_at(session: dict[str, Any], path: Path) -> str:
    value = session.get("updated_at") or session.get("created_at")
    if isinstance(value, str) and value:
        return value
    timestamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return timestamp.isoformat()


def summarize_session(path: Path) -> dict[str, Any] | None:
    """Return comparison metrics for one completed paper-review session."""
    try:
        session = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    if session.get("status") != "completed":
        return None
    result = session.get("result")
    if not isinstance(result, dict):
        return None

    metadata = _metadata(session)
    review_outputs = _phase_outputs(result, "independent_review")
    meta_outputs = _phase_outputs(result, "meta_review")

    review_scores: list[int] = []
    malformed_reviews = 0
    strengths = 0
    weaknesses = 0
    questions = 0
    revision_actions = 0
    reviewer_models: set[str] = set()

    for output in review_outputs:
        metadata_out = output.get("metadata")
        metadata_out = metadata_out if isinstance(metadata_out, dict) else {}
        model = metadata_out.get("model")
        if isinstance(model, str) and model:
            reviewer_models.add(model)
        content = output.get("content", "")
        parsed = _json_object_from_text(content if isinstance(content, str) else "")
        if not parsed:
            malformed_reviews += 1
            continue
        score = _score(parsed.get("overall_merit"))
        if score is not None:
            review_scores.append(score)
        strengths += _list_len(parsed.get("strengths"))
        weaknesses += _list_len(parsed.get("weaknesses"))
        questions += _list_len(parsed.get("questions_for_authors"))
        revision_actions += _list_len(parsed.get("revision_actions"))

    meta_json_ok = False
    for output in meta_outputs:
        content = output.get("content", "")
        if _json_object_from_text(content if isinstance(content, str) else ""):
            meta_json_ok = True
            break

    final_review = result.get("final_review") or result.get("pc_chair_review") or {}
    final_score = None
    if isinstance(final_review, dict):
        final_score = _score(
            final_review.get("overall_merit")
            or final_review.get("final_recommendation")
        )

    graph = session.get("knowledge_graph")
    graph_nodes = len(graph.get("nodes", [])) if isinstance(graph, dict) else 0
    graph_edges = len(graph.get("edges", [])) if isinstance(graph, dict) else 0
    app_data = session.get("app_data")
    app_data = app_data if isinstance(app_data, dict) else {}
    parse = app_data.get("parse", {})
    if not isinstance(parse, dict):
        parse = {}

    hidden_hits = [
        label for label, text in _text_fields(session)
        if HIDDEN_REASONING_RE.search(text)
    ]
    score_spread = (
        max(review_scores) - min(review_scores)
        if len(review_scores) >= 2
        else 0
    )
    flags: list[str] = []
    if not review_outputs:
        flags.append("no_individual_reviews")
    if malformed_reviews:
        flags.append(f"malformed_review_json:{malformed_reviews}")
    if not meta_json_ok:
        flags.append("missing_meta_json")
    if not isinstance(final_review, dict) or not final_review:
        flags.append("missing_final_review")
    if hidden_hits:
        flags.append(f"hidden_reasoning:{len(hidden_hits)}")
    if graph_nodes == 0:
        flags.append("no_graph")
    if revision_actions == 0:
        flags.append("no_revision_actions")
    if score_spread > 2:
        flags.append(f"high_score_spread:{score_spread}")

    return {
        "session_id": session.get("session_id") or path.stem,
        "path": str(path),
        "updated_at": _updated_at(session, path),
        "conference": metadata.get("conference", ""),
        "paper_title": metadata.get("paper_title") or metadata.get("filename", ""),
        "filename": metadata.get("filename", ""),
        "reviewer_count": len(review_outputs),
        "reviewer_models": sorted(reviewer_models),
        "malformed_review_json": malformed_reviews,
        "meta_json_ok": meta_json_ok,
        "final_review_present": bool(final_review),
        "review_scores": review_scores,
        "review_score_mean": round(mean(review_scores), 2) if review_scores else None,
        "review_score_spread": score_spread,
        "final_score": final_score,
        "strength_count": strengths,
        "weakness_count": weaknesses,
        "question_count": questions,
        "revision_action_count": revision_actions,
        "graph_node_count": graph_nodes,
        "graph_edge_count": graph_edges,
        "figure_count": parse.get("figure_count", 0),
        "table_count": parse.get("table_count", 0),
        "vlm_enabled": bool(parse.get("vlm", {}).get("enabled"))
        if isinstance(parse.get("vlm"), dict)
        else False,
        "hidden_reasoning_hits": hidden_hits,
        "quality_flags": flags,
    }


def collect_sessions(
    sessions_dir: Path,
    *,
    session_ids: list[str] | None = None,
    latest_completed: int | None = None,
) -> list[dict[str, Any]]:
    """Load completed session summaries from a session artifact directory."""
    wanted = set(session_ids or [])
    records: list[dict[str, Any]] = []
    for path in sorted(sessions_dir.glob("*.json")):
        if wanted and path.stem not in wanted:
            continue
        record = summarize_session(path)
        if record:
            records.append(record)

    records.sort(key=lambda item: item["updated_at"], reverse=True)
    if latest_completed is not None:
        records = records[:latest_completed]
    return records


def build_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build aggregate comparison data for report serialization."""
    hidden_count = sum(len(r["hidden_reasoning_hits"]) for r in records)
    missing_final = sum(1 for r in records if not r["final_review_present"])
    malformed = sum(r["malformed_review_json"] for r in records)
    graphless = sum(1 for r in records if r["graph_node_count"] == 0)
    score_spreads = [r["review_score_spread"] for r in records]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "paper_count": len(records),
        "summary": {
            "hidden_reasoning_violations": hidden_count,
            "missing_final_reviews": missing_final,
            "malformed_review_json_outputs": malformed,
            "graphless_sessions": graphless,
            "max_review_score_spread": max(score_spreads) if score_spreads else 0,
            "mean_review_score_spread": round(mean(score_spreads), 2)
            if score_spreads
            else 0,
        },
        "papers": records,
    }


def _cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def report_to_markdown(report: dict[str, Any]) -> str:
    """Render a report as a compact Markdown comparison table."""
    summary = report["summary"]
    lines = [
        "# Paper Review Quality Comparison",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Papers compared: **{report['paper_count']}**",
        "",
        "## Aggregate Checks",
        "",
        f"- Hidden reasoning violations: {summary['hidden_reasoning_violations']}",
        f"- Missing final reviews: {summary['missing_final_reviews']}",
        f"- Malformed reviewer JSON outputs: {summary['malformed_review_json_outputs']}",
        f"- Graphless sessions: {summary['graphless_sessions']}",
        f"- Max reviewer score spread: {summary['max_review_score_spread']}",
        f"- Mean reviewer score spread: {summary['mean_review_score_spread']}",
        "",
        "## Per-Paper Comparison",
        "",
        "| Session | Conference | Paper | Reviewers | Scores | Final | Graph | Figs/Tables | Flags |",
        "| --- | --- | --- | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for record in report["papers"]:
        scores = ",".join(str(score) for score in record["review_scores"])
        graph = f"{record['graph_node_count']}/{record['graph_edge_count']}"
        figures = f"{record['figure_count']}/{record['table_count']}"
        flags = ", ".join(record["quality_flags"]) or "none"
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(record["session_id"][:8]),
                    _cell(record["conference"]),
                    _cell(record["paper_title"] or record["filename"]),
                    _cell(record["reviewer_count"]),
                    _cell(scores),
                    _cell(record["final_score"]),
                    _cell(graph),
                    _cell(figures),
                    _cell(flags),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def _write_json(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_to_markdown(report))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare completed Paper Review sessions across papers."
    )
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        default=Path("data/sessions"),
        help="Directory containing session JSON artifacts.",
    )
    parser.add_argument(
        "--session-id",
        action="append",
        default=[],
        help="Specific session ID to include. May be repeated.",
    )
    parser.add_argument(
        "--latest-completed",
        type=int,
        help="Compare only the N most recently updated completed sessions.",
    )
    parser.add_argument("--json", type=Path, help="Write machine-readable JSON.")
    parser.add_argument("--markdown", type=Path, help="Write Markdown report.")
    parser.add_argument(
        "--fail-on-hidden-reasoning",
        action="store_true",
        help="Exit nonzero if stored outputs contain hidden reasoning markers.",
    )
    parser.add_argument(
        "--fail-on-missing-final",
        action="store_true",
        help="Exit nonzero if any compared session lacks a final review.",
    )
    args = parser.parse_args(argv)

    records = collect_sessions(
        args.sessions_dir,
        session_ids=args.session_id,
        latest_completed=args.latest_completed,
    )
    report = build_report(records)

    if args.json:
        _write_json(report, args.json)
    if args.markdown:
        _write_markdown(report, args.markdown)
    if not args.json and not args.markdown:
        sys.stdout.write(report_to_markdown(report))

    summary = report["summary"]
    if args.fail_on_hidden_reasoning and summary["hidden_reasoning_violations"]:
        return 1
    if args.fail_on_missing_final and summary["missing_final_reviews"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
