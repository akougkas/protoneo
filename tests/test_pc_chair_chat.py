import json
from types import SimpleNamespace

import pytest

from apps.paper_review import api


class _FakeSessionManager:
    def __init__(self, session):
        self.session = session
        self.updated = False

    async def get(self, session_id):
        return self.session if session_id == "session-1" else None

    async def update(self, session):
        self.session = session
        self.updated = True


class _FakeLLMClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=json.dumps(self.payload))


def _session():
    return SimpleNamespace(
        config={
            "metadata": {"conference": "adaptive", "paper_title": "VisionHPC"},
            "agents": {"meta": {"model": "mini/reviewer"}},
        },
        result={
            "phases": [],
            "final_review": {
                "overall_merit": {
                    "score": 3,
                    "label": "Borderline",
                    "rationale": "Mixed evidence.",
                },
                "comments_for_authors": "The paper needs clearer evaluation detail.",
            },
        },
        app_data={},
        document_markdown="## Paper\nVisionHPC evaluates GPU kernels.",
        document_text="VisionHPC evaluates GPU kernels.",
        knowledge_graph={
            "summary": "Paper graph summary.",
            "nodes": [{"id": "paper-root"}],
            "edges": [],
        },
        current_stage="post_review",
    )


@pytest.mark.asyncio
async def test_pc_chair_chat_stages_focused_artifact_and_blocks_silent_decision_change(monkeypatch):
    session = _session()
    manager = _FakeSessionManager(session)
    llm = _FakeLLMClient(
        {
            "reply": "I tightened the wording and left the score unchanged.",
            "edit_summary": ["Revised author-facing wording."],
            "final_review_patch": {
                "overall_merit": {
                    "score": 4,
                    "label": "Accept",
                    "rationale": "Evidence is stronger in the revised wording.",
                },
                "comments_for_authors": "Clarify the evaluation setup and baseline tuning.",
            },
            "focused_artifacts": [{"id": "final:comments_for_authors"}],
            "needs_user_decision": False,
        }
    )
    monkeypatch.setattr(api, "get_session_manager", lambda: manager)
    monkeypatch.setattr(api, "get_llm_client", lambda: llm)

    response = await api.pc_chair_chat(
        "session-1",
        api.PCChairChatRequest(
            message="Tighten the author-facing comments.",
            current_review=session.result["final_review"],
            apply_edits=False,
            user_role="author",
            focused_artifact={
                "id": "final:comments_for_authors",
                "type": "final_field",
                "label": "Comments For Authors",
                "excerpt": "The paper needs clearer evaluation detail.",
            },
            selected_review_field="comments_for_authors",
            selected_review_excerpt="The paper needs clearer evaluation detail.",
        ),
    )

    assert response["applied_edits"] is False
    assert response["needs_user_decision"] is True
    assert response["final_review_patch"]["overall_merit"] == {
        "rationale": "Evidence is stronger in the revised wording."
    }
    assert response["final_review_patch"]["comments_for_authors"].startswith("Clarify")
    assert session.result["final_review"]["overall_merit"]["score"] == 3

    turn = session.app_data["pc_chair_chat"][0]
    assert turn["user_role"] == "author"
    assert turn["focused_artifact"]["id"] == "final:comments_for_authors"
    assert turn["selected_review_field"] == "comments_for_authors"
    assert any("overall_merit.score" in item for item in turn["edit_summary"])
    assert manager.updated is True

    user_prompt = llm.calls[0]["messages"][1]["content"]
    assert "User role/persona:\nauthor" in user_prompt
    assert '"id": "final:comments_for_authors"' in user_prompt


@pytest.mark.asyncio
async def test_pc_chair_chat_applies_explicit_decision_change(monkeypatch):
    session = _session()
    manager = _FakeSessionManager(session)
    llm = _FakeLLMClient(
        {
            "reply": "I changed the decision fields as requested.",
            "edit_summary": ["Updated overall merit."],
            "final_review_patch": {
                "overall_merit": {"score": 4, "label": "Accept", "rationale": "Above the bar."},
                "comments_for_pc": "Score changed by explicit chair request.",
            },
            "needs_user_decision": False,
        }
    )
    monkeypatch.setattr(api, "get_session_manager", lambda: manager)
    monkeypatch.setattr(api, "get_llm_client", lambda: llm)

    response = await api.pc_chair_chat(
        "session-1",
        api.PCChairChatRequest(
            message="Change the overall merit score to 4 and recommendation to accept.",
            current_review=session.result["final_review"],
            apply_edits=True,
        ),
    )

    assert response["applied_edits"] is True
    assert response["needs_user_decision"] is False
    assert response["final_review"]["overall_merit"]["score"] == 4
    assert session.result["final_review"]["overall_merit"]["label"] == "Accept"
    assert session.app_data["pc_chair_chat"][0]["applied"] is True
