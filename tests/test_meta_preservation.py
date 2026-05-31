import json

from apps.paper_review import review as rv
from apps.paper_review.conference import ConferenceProfile
from apps.paper_review.export import packet_to_markdown
from protoneo.agents.types import AgentOutput
from protoneo.deliberation.types import DeliberationResult, PhaseResult

PANEL = {
    "panel_summary": "P",
    "score_distribution": {"3": 2, "4": 1},
    "consensus": {"label": "Borderline"},
    "agreements": ["a"],
    "disagreements": ["d"],
    "final_recommendation": {"score": 3, "label": "Borderline"},
    "author_facing_summary": "AF",
    "prioritized_revision_plan": [{"action": "x"}],
    "strengths": ["s"],
    "weaknesses": ["w"],
    "revision_actions": ["r"],
}


def _meta_output() -> AgentOutput:
    return AgentOutput(
        agent_id="meta_1",
        agent_role="Meta-Reviewer",
        content=json.dumps(PANEL),
    )


def test_meta_fields_survive_parse():
    meta_review = rv.parse_meta_review(_meta_output())
    for key in (
        "panel_summary",
        "score_distribution",
        "consensus",
        "agreements",
        "disagreements",
        "final_recommendation",
        "author_facing_summary",
        "prioritized_revision_plan",
    ):
        assert getattr(meta_review, key), f"{key} dropped by parse_meta_review"


def test_meta_fields_survive_packet_and_export():
    result = DeliberationResult(
        session_id="sid",
        phases=[
            PhaseResult(
                phase_name="meta_review",
                mode="sequential",
                outputs=[_meta_output()],
            )
        ],
    )
    packet = rv.result_to_packet(
        result,
        ConferenceProfile(slug="test", name="Test"),
        paper_title="Paper",
    )
    assert packet.meta_review.panel_summary == "P"
    assert packet.meta_review.score_distribution == {"3": 2, "4": 1}
    assert packet.meta_review.author_facing_summary == "AF"
    assert packet.meta_review.prioritized_revision_plan == [{"action": "x"}]
    assert "author_facing_summary" in packet.model_dump_json()
    assert "prioritized_revision_plan" in packet.model_dump_json()
    markdown = packet_to_markdown(packet)
    assert "AF" in markdown or "P" in markdown
    assert "Prioritized Revision Plan" in markdown
    assert "x" in markdown
