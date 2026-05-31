from types import SimpleNamespace

import pytest

from apps.paper_review.pipeline import (
    _build_enriched_review_message,
    _build_visual_evidence_ledger,
    _run_review_stage,
)
from protoneo.agents.types import AgentOutput
from protoneo.deliberation.types import DeliberationResult, PhaseResult
from protoneo.knowledge.graph import KnowledgeGraph


def _g():
    graph = KnowledgeGraph()
    graph.add_node("P", "Paper", node_id="paper-root")
    graph.ingest_visual_evidence(
        [
            {
                "index": 1,
                "kind": "figure",
                "page": 3,
                "bbox": {},
                "caption": "Speedup",
                "image_path": "/f1.png",
                "description": "Bar chart; 4.2x over OpenMP.",
                "description_source": "vlm",
                "numeric_claims": ["4.2x over OpenMP"],
                "model": "omni",
                "endpoint": "u",
                "grounding": "visual",
                "confidence": 0.6,
            }
        ],
        [],
    )
    return graph


def test_ledger_lists_described_artifacts_only():
    ledger = _build_visual_evidence_ledger(_g())
    assert "Figure 1" in ledger
    assert "4.2x over OpenMP" in ledger
    assert "image_path" not in ledger


def test_enriched_message_includes_ledger():
    message = _build_enriched_review_message("USER MSG", _g())
    assert "USER MSG" in message
    assert "Visual Evidence Ledger" in message
    assert "4.2x over OpenMP" in message


@pytest.mark.asyncio
async def test_rendered_prompt_snapshot_recorded(monkeypatch):
    import apps.paper_review.pipeline as pipeline

    session = SimpleNamespace(
        app_data={},
        result=None,
        status=None,
        checkpoints=[],
        last_checkpoint="",
        config={"metadata": {}},
    )

    class FakeSessionManager:
        async def get(self, sid):
            return session

        async def update(self, updated):
            session.app_data = updated.app_data

    final_output = AgentOutput(
        agent_id="meta",
        agent_role="Meta",
        content='{"panel_summary":"ok"}',
    )
    result = DeliberationResult(
        session_id="sid",
        phases=[PhaseResult(phase_name="meta_review", mode="sequential", outputs=[final_output])],
        final_output=final_output,
    )

    class FakeEngine:
        async def run(self, **kwargs):
            kwargs["on_event"](
                "prompt_rendered",
                {"phase": "deliberation", "text": "DELTA PROMPT"},
            )
            return result

    async def fake_finalize(*args, **kwargs):
        return None

    monkeypatch.setattr(pipeline, "get_engine", lambda: FakeEngine())
    monkeypatch.setattr(pipeline, "get_session_manager", lambda: FakeSessionManager())
    monkeypatch.setattr(pipeline, "review_web_search_enabled", lambda: False)
    monkeypatch.setattr(pipeline, "_finalize_unified_synthesis", fake_finalize)

    ctl = SimpleNamespace(current_step="independent_reviews", enter_step=lambda step: None)
    bus = SimpleNamespace(emit=lambda *args, **kwargs: None)
    await _run_review_stage(
        "sid",
        agent_configs={},
        delib_config=SimpleNamespace(),
        enriched_message="USER MSG ... Visual Evidence Ledger",
        bus=bus,
        ctl=ctl,
        paper_graph=KnowledgeGraph(),
    )

    prompts = session.app_data["rendered_prompts"]
    assert "Visual Evidence Ledger" in prompts["independent_review"]
    assert prompts["deliberation"] == "DELTA PROMPT"
