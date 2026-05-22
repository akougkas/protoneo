"""Tests for the Paper Review multi-paper comparison harness."""

import importlib.util
import json
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "paper_review_quality_compare.py"
)
SPEC = importlib.util.spec_from_file_location("paper_review_quality_compare", SCRIPT_PATH)
quality_compare = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(quality_compare)


def _write_session(path: Path, session_id: str, *, hidden: bool = False) -> None:
    review_payload = json.dumps({
        "summary": "Good paper.",
        "overall_merit": {"score": 4},
        "strengths": ["solid evaluation"],
        "weaknesses": ["minor clarity issue"],
        "questions_for_authors": ["Can you clarify the setup?"],
        "revision_actions": ["Tighten related work."],
    })
    if hidden:
        review_payload = f"<think>private notes</think>{review_payload}"

    session = {
        "session_id": session_id,
        "status": "completed",
        "updated_at": f"2026-05-22T12:00:0{session_id[-1]}+00:00",
        "config": {
            "metadata": {
                "conference": "hpdc26",
                "filename": f"{session_id}.pdf",
                "paper_title": f"Paper {session_id}",
            }
        },
        "knowledge_graph": {
            "nodes": [{"id": "n1"}, {"id": "n2"}],
            "edges": [{"source": "n1", "target": "n2"}],
        },
        "app_data": {
            "parse": {
                "figure_count": 2,
                "table_count": 1,
                "vlm": {"enabled": True},
            }
        },
        "result": {
            "phases": [
                {
                    "phase_name": "independent_review",
                    "outputs": [
                        {
                            "agent_id": "technical",
                            "content": review_payload,
                            "metadata": {"model": "test/model"},
                        }
                    ],
                },
                {
                    "phase_name": "meta_review",
                    "outputs": [
                        {
                            "agent_id": "meta",
                            "content": json.dumps({
                                "panel_summary": "Consensus accept.",
                                "final_recommendation": {"score": 4},
                            }),
                        }
                    ],
                },
            ],
            "final_review": {
                "overall_merit": {"score": 4},
                "paper_summary": "Final review.",
            },
        },
    }
    path.write_text(json.dumps(session))


def test_summarize_session_records_quality_metrics(tmp_path):
    path = tmp_path / "s1.json"
    _write_session(path, "s1", hidden=True)

    record = quality_compare.summarize_session(path)

    assert record["session_id"] == "s1"
    assert record["reviewer_count"] == 1
    assert record["review_scores"] == [4]
    assert record["final_score"] == 4
    assert record["graph_node_count"] == 2
    assert record["figure_count"] == 2
    assert record["hidden_reasoning_hits"] == ["independent_review:technical"]
    assert "hidden_reasoning:1" in record["quality_flags"]


def test_quality_compare_cli_writes_json_and_markdown(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    _write_session(sessions_dir / "s1.json", "s1")
    _write_session(sessions_dir / "s2.json", "s2", hidden=True)
    json_path = tmp_path / "report.json"
    md_path = tmp_path / "report.md"

    code = quality_compare.main([
        "--sessions-dir",
        str(sessions_dir),
        "--latest-completed",
        "2",
        "--json",
        str(json_path),
        "--markdown",
        str(md_path),
    ])

    assert code == 0
    report = json.loads(json_path.read_text())
    assert report["paper_count"] == 2
    assert report["summary"]["hidden_reasoning_violations"] == 1
    markdown = md_path.read_text()
    assert "Paper Review Quality Comparison" in markdown
    assert "hidden_reasoning:1" in markdown
