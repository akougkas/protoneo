import json
from types import SimpleNamespace

import pytest

from apps.paper_review import api
from apps.paper_review.conference import load_profile
from apps.paper_review.export import packet_to_markdown, write_review_artifacts
from apps.paper_review.review import result_to_packet
from apps.paper_review.review_context import ReviewContextMode
from apps.paper_review.schemas import MetaReview, ReviewPacket
from protoneo.agents.types import AgentOutput
from protoneo.deliberation.types import DeliberationResult, PhaseResult


def _review_output(agent_id: str, role: str, score: int) -> AgentOutput:
    payload = {
        "summary": f"{role} summary.",
        "overall_merit": {"score": score, "label": f"Score {score}"},
        "strengths": [{"point": "Relevant problem."}],
        "weaknesses": [{"point": "Needs stronger evidence."}],
        "comments_for_authors": "Please strengthen the evaluation.",
        "confidence": {"score": 3, "label": "Medium"},
    }
    return AgentOutput(
        agent_id=agent_id,
        agent_role=role,
        content=json.dumps(payload),
        structured=payload,
    )


def _stance_output(agent_id: str, role: str, current_score: int, round_number: int = 1) -> AgentOutput:
    payload = {
        "stance_change": {
            "changed": True,
            "previous_score": 3,
            "current_score": current_score,
            "reason": "Deliberation changed the score.",
        },
        "strongest_agreement": {},
        "strongest_disagreement": {},
        "evidence_correction": {},
        "include_in_final_review": {},
        "exclude_from_final_review": {},
    }
    return AgentOutput(
        agent_id=agent_id,
        agent_role=role,
        content=json.dumps(payload),
        structured=payload,
        metadata={"round": round_number, "speaker_id": agent_id},
    )


def test_result_to_packet_preserves_initial_and_final_score_distributions():
    result = DeliberationResult(
        session_id="session-score",
        phases=[
            PhaseResult(
                phase_name="independent_review",
                mode="parallel",
                outputs=[
                    _review_output("technical", "Technical Reviewer", 3),
                    _review_output("systems", "Systems Reviewer", 3),
                ],
            ),
            PhaseResult(
                phase_name="deliberation",
                mode="round_robin",
                outputs=[
                    _stance_output("technical", "Technical Reviewer", 2),
                    _stance_output("systems", "Systems Reviewer", 2),
                ],
            ),
            PhaseResult(
                phase_name="meta_review",
                mode="sequential",
                outputs=[
                    AgentOutput(
                        agent_id="meta",
                        agent_role="Meta-Reviewer",
                        content=json.dumps(
                            {
                                "panel_summary": "The panel moved lower after deliberation.",
                                "score_distribution": {"technical": 3, "systems": 3},
                                "final_recommendation": {"score": 2, "label": "Reject"},
                            }
                        ),
                    )
                ],
            ),
        ],
    )

    packet = result_to_packet(result, load_profile("adaptive"), paper_title="Synthetic")

    assert packet.meta_review.initial_score_distribution == {"technical": 3, "systems": 3}
    assert packet.meta_review.current_score_distribution == {"technical": 2, "systems": 2}
    assert packet.meta_review.final_score_distribution == {"technical": 2, "systems": 2}
    assert packet.meta_review.score_distribution == {"technical": 2, "systems": 2}

    markdown = packet_to_markdown(packet)
    assert "Initial Score Distribution" in markdown
    assert "Final Score Distribution After Deliberation" in markdown


@pytest.mark.asyncio
async def test_write_review_artifacts_preserves_run_scoped_packet_output(tmp_path):
    packet_dir = tmp_path / "papunit"
    packet_dir.mkdir()
    session = SimpleNamespace(
        session_id="session-run",
        config={
            "metadata": {
                "conference": "adaptive",
                "paper_title": "Synthetic",
                "packet_dir": str(packet_dir),
                "packet_paper_id": "papunit",
                "run_id": "run-unit",
                "context_mode": ReviewContextMode.MARKDOWN_ONLY.value,
                "artifact_description_status": "submitted",
                "artifact_description_assumed_present": True,
            },
            "agents": {},
        },
        result={
            "session_id": "session-run",
            "phases": [
                {
                    "phase_name": "independent_review",
                    "mode": "parallel",
                    "outputs": [
                        _review_output("technical", "Technical Reviewer", 3).model_dump(mode="json")
                    ],
                }
            ],
            "final_output": None,
            "duration_seconds": 0.1,
            "total_cost": 0,
            "metadata": {},
        },
        app_data={},
        knowledge_graph=None,
    )

    manifest, output_dir = await api._write_review_artifacts_for_session(session)

    expected_dir = packet_dir / "protoneo_outputs" / "run-unit"
    assert output_dir == expected_dir
    assert (expected_dir / "run_manifest.json").exists()
    assert not (packet_dir / "protoneo_outputs" / "run_manifest.json").exists()
    assert manifest["run_id"] == "run-unit"
    assert manifest["context_mode"] == ReviewContextMode.MARKDOWN_ONLY.value
    assert manifest["artifact_paths"]["meta_review.json"].startswith(str(expected_dir))


def test_write_review_artifacts_uses_packet_provenance_for_manifest(tmp_path):
    packet = ReviewPacket(
        session_id="session-provenance",
        conference="adaptive",
        paper_title="Synthetic",
        meta_review=MetaReview(panel_summary="Done."),
        provenance_metadata={
            "session_metadata": {
                "run_id": "run-packet",
                "context_mode": ReviewContextMode.MARKDOWN_PLUS_REQUIRED_GRAPH_CITATIONS.value,
            }
        },
    )

    manifest = write_review_artifacts(packet, tmp_path / "out", paper_id="papunit")

    assert manifest["run_id"] == "run-packet"
    assert manifest["context_mode"] == ReviewContextMode.MARKDOWN_PLUS_REQUIRED_GRAPH_CITATIONS.value
