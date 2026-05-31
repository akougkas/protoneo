import json

import pytest

from protoneo.agents.types import Message
from protoneo.deliberation.patterns import (
    RoundRobinPattern,
    _coerce_to_delta,
    _is_delta_json,
    _is_full_review_json,
)
from protoneo.deliberation.session import SessionContext
from protoneo.deliberation.types import DeliberationRules

FULL = {
    "overall_merit": {"score": 3},
    "strengths": ["a"],
    "weaknesses": ["b"],
    "paper_summary": "x",
    "technical_soundness": {"score": 3},
}
DELTA = {
    "stance_change": {"changed": False, "current_score": 3, "reason": ""},
    "strongest_agreement": {"with_reviewer": "Technical", "issue": "y", "evidence": "z"},
    "strongest_disagreement": {},
    "evidence_correction": {},
    "include_in_final_review": {},
    "exclude_from_final_review": {},
}


def test_classifiers():
    assert _is_full_review_json(FULL) and not _is_delta_json(FULL)
    assert _is_delta_json(DELTA) and not _is_full_review_json(DELTA)


def test_coerce_full_review_to_delta():
    delta = _coerce_to_delta(FULL)
    assert _is_delta_json(delta)
    assert "stance_change" in delta
    assert delta["stance_change"]["current_score"] == 3


@pytest.mark.asyncio
async def test_round_robin_coerces_repeated_full_review():
    class FullReviewAgent:
        agent_id = "technical"
        role = "Technical"
        model = "fake"

        async def process(self, context, message, include_history=False):
            return Message(
                role="assistant",
                content=json.dumps(FULL),
                agent_id=self.agent_id,
            )

    pattern = RoundRobinPattern()
    result = await pattern.execute(
        [FullReviewAgent()],
        SessionContext("sid"),
        DeliberationRules(max_rounds=1),
        on_event=None,
        stream=False,
        paper_context="paper",
    )
    assert result.outputs[0].structured["_recovered_from_full_review"] is True
