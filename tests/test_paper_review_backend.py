import asyncio
import json
from types import SimpleNamespace

import pytest

from apps.paper_review import api
from apps.paper_review.conference import ConferenceProfile
from apps.paper_review.export import packet_to_markdown
from apps.paper_review.pipeline import _parse_final_review
from apps.paper_review.review import (
    parse_review_output,
    resolve_paper_review_model,
    session_to_review_packet,
)
from apps.paper_review.schemas import ReviewPacket
from protoneo.agents.base import BaseAgent
from protoneo.agents.types import AgentOutput, Message
from protoneo.config.schema import AgentConfig
from protoneo.deliberation.session import (
    SessionContext,
    SessionStatus,
    StageCheckpoint,
    StepState,
)
from protoneo.deliberation.types import DeliberationResult
from protoneo.llm.settings import LocalEndpoint, ModelPreset, ProtoNeoSettings


class FakeSessionManager:
    def __init__(self, session):
        self.session = session
        self.updated = []

    async def get(self, session_id):
        return self.session

    async def update(self, session):
        self.updated.append(session)
        self.session = session


class FakeMultiSessionManager:
    def __init__(self, sessions):
        self.sessions = sessions
        self.updated = []

    async def get(self, session_id):
        return self.sessions.get(session_id)

    async def update(self, session):
        self.updated.append(session)
        self.sessions[session.session_id] = session


class FakeBatchManager:
    def __init__(self, batch):
        self.batch = batch

    async def get(self, batch_id):
        return self.batch


def _agent_config(model: str = "lan-dynamo/model") -> AgentConfig:
    return AgentConfig(
        role="Technical Reviewer",
        model=model,
        system_prompt="Review.",
        top_k=20,
        min_p=0.05,
        repeat_penalty=1.05,
        reasoning_effort="medium",
    )


def _retry_session(mode: str, *, checkpoints=None):
    cfg = _agent_config()
    return SimpleNamespace(
        session_id="s1",
        status=SessionStatus.FAILED,
        config={
            "agents": {"technical": cfg.model_dump()},
            "deliberation": {},
            "metadata": {
                "conference": "hpdc26",
                "filename": "paper.pdf",
                "pipeline_mode": mode,
            },
        },
        document_text="paper text",
        document_markdown="paper markdown",
        pipeline_steps={},
        checkpoints=checkpoints or [],
        app_data={},
        error="failed",
        current_stage="",
        knowledge_graph=None,
        graph_source="extracted",
    )


def test_parse_final_review_sanitizes_malformed_fields_for_export():
    raw = json.dumps({
        "final_review": {
            "overall_merit": "high",
            "reviewer_expertise": ["bad"],
            "strengths": "strong evaluation",
            "weaknesses": {"point": "thin related work"},
            "submission_readiness": "ready",
        }
    })

    final_review = _parse_final_review(raw)

    assert isinstance(final_review["overall_merit"], dict)
    assert isinstance(final_review["reviewer_expertise"], dict)
    assert isinstance(final_review["strengths"], list)
    assert isinstance(final_review["weaknesses"], list)
    assert final_review["submission_readiness"]["status"] == "ready"

    packet = ReviewPacket(
        session_id="s1",
        conference="hpdc26",
        pc_chair_review=final_review,
    )
    assert "Final Review" in packet_to_markdown(packet)


def test_parse_final_review_strips_leaked_chain_of_thought():
    raw = (
        "Reasoning: private synthesis notes.\n"
        + json.dumps({
            "final_review": {
                "paper_summary": "Public final review.",
                "comments_for_authors": "Visible author comments.",
            }
        })
    )

    final_review = _parse_final_review(raw)

    assert final_review["paper_summary"] == "Public final review."
    assert "private synthesis" not in final_review["comments_for_authors"]


@pytest.mark.asyncio
async def test_update_final_review_sanitizes_and_invalidates_cached_packet(monkeypatch):
    session = SimpleNamespace(
        result={"final_review": {}, "pc_chair_review": {}},
        app_data={"review_packet": {"stale": True}},
    )
    manager = FakeSessionManager(session)
    monkeypatch.setattr(api, "get_session_manager", lambda: manager)

    body = api.UpdateFinalReviewRequest(
        final_review={
            "overall_merit": 5,
            "reviewer_expertise": "expert",
            "submission_readiness": "ready",
        }
    )
    response = await api.update_final_review("s1", body)

    assert response == {"status": "saved"}
    assert "review_packet" not in session.app_data
    assert session.result["final_review"]["overall_merit"]["score"] == 5
    assert session.result["final_review"]["reviewer_expertise"]["label"] == "expert"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "checkpoints", "expected_graph_only", "expected_skip_gate"),
    [
        ("full_review", [], False, True),
        ("full_review", [StageCheckpoint(stage_name="summary")], False, True),
        ("graph_only", [], True, False),
    ],
)
async def test_retry_session_preserves_intended_pipeline_mode(
    monkeypatch,
    mode,
    checkpoints,
    expected_graph_only,
    expected_skip_gate,
):
    session = _retry_session(mode, checkpoints=checkpoints)
    manager = FakeSessionManager(session)
    calls = []

    async def fake_run_graph_pipeline(*args, **kwargs):
        ctl = args[6]
        calls.append({
            "graph_only": kwargs["graph_only"],
            "skip_gate": ctl.skip_gate,
        })

    monkeypatch.setattr(api, "get_session_manager", lambda: manager)
    monkeypatch.setattr(api, "load_profile", lambda slug: ConferenceProfile(slug=slug, name=slug))
    monkeypatch.setattr(api, "_run_graph_pipeline", fake_run_graph_pipeline)

    response = await api.retry_session("s1")
    await asyncio.sleep(0)

    assert response["action"] == "retry"
    assert calls == [{
        "graph_only": expected_graph_only,
        "skip_gate": expected_skip_gate,
    }]


@pytest.mark.asyncio
async def test_retry_imported_graph_session_runs_review_only(monkeypatch):
    session = _retry_session("imported_graph_review")
    session.document_text = ""
    session.document_markdown = ""
    session.knowledge_graph = {"nodes": [], "edges": [], "summary": "graph"}
    session.graph_source = "imported"
    manager = FakeSessionManager(session)
    calls = []

    async def fake_run_review_only(*args, **kwargs):
        calls.append(args[0])

    monkeypatch.setattr(api, "get_session_manager", lambda: manager)
    monkeypatch.setattr(api, "load_profile", lambda slug: ConferenceProfile(slug=slug, name=slug))
    monkeypatch.setattr(api, "_run_review_only_pipeline", fake_run_review_only)

    response = await api.retry_session("s1")
    await asyncio.sleep(0)

    assert response["action"] == "retry"
    assert calls == ["s1"]


@pytest.mark.asyncio
async def test_batch_retry_preserves_graph_only_and_full_review_modes(monkeypatch):
    graph_session = _retry_session("graph_only")
    graph_session.session_id = "graph"
    full_session = _retry_session("full_review")
    full_session.session_id = "full"
    batch = SimpleNamespace(batch_id="b1", session_ids=["graph", "full"])
    manager = FakeMultiSessionManager({
        "graph": graph_session,
        "full": full_session,
    })
    calls = []

    async def fake_run_graph_pipeline(*args, **kwargs):
        sid = args[0]
        ctl = args[6]
        calls.append({
            "sid": sid,
            "graph_only": kwargs["graph_only"],
            "skip_gate": ctl.skip_gate,
        })

    monkeypatch.setattr(api, "get_session_manager", lambda: manager)
    monkeypatch.setattr(api, "get_batch_manager", lambda: FakeBatchManager(batch))
    monkeypatch.setattr(api, "get_event_buses", lambda: {})
    monkeypatch.setattr(api, "get_pipeline_controls", lambda: {})
    monkeypatch.setattr(api, "load_profile", lambda slug: ConferenceProfile(slug=slug, name=slug))
    monkeypatch.setattr(api, "_run_graph_pipeline", fake_run_graph_pipeline)

    response = await api.retry_failed_in_batch("b1")
    for _ in range(10):
        if len(calls) == 2:
            break
        await asyncio.sleep(0)

    assert response["retried"] == 2
    assert calls == [
        {"sid": "graph", "graph_only": True, "skip_gate": False},
        {"sid": "full", "graph_only": False, "skip_gate": True},
    ]


def test_active_preset_drives_local_graph_model_routing(monkeypatch):
    settings = ProtoNeoSettings(
        lan_endpoints=[
            LocalEndpoint(
                id="lan-mini",
                display_name="Mini",
                url="http://mini/v1",
                location="lan",
            ),
            LocalEndpoint(
                id="lan-dynamo",
                display_name="Dynamo",
                url="http://dynamo/v1",
                location="lan",
            ),
        ],
        active_models={
            "lan-mini": "mini-first",
            "lan-dynamo": "dynamo-active",
        },
        active_preset="nemotron-omni-split",
    )

    monkeypatch.setattr("protoneo.llm.settings.load_settings", lambda: settings)

    model = resolve_paper_review_model("ontology", require_local=True)

    assert model.startswith("lan-dynamo/")
    assert "omni" in model


def test_graph_routing_skips_reasoning_preset_when_fast_model_available(monkeypatch):
    settings = ProtoNeoSettings(
        lan_endpoints=[
            LocalEndpoint(
                id="lan-mini",
                display_name="Mini",
                url="http://mini/v1",
                location="lan",
            ),
            LocalEndpoint(
                id="lan-dynamo",
                display_name="Dynamo",
                url="http://dynamo/v1",
                location="lan",
            ),
        ],
        active_models={
            "lan-mini": "fast-json",
            "lan-dynamo": "reasoning-model",
        },
        presets=[
            ModelPreset(
                name="bad-graph",
                assignments={"ontology": "lan-dynamo/reasoning-model"},
            )
        ],
        active_preset="bad-graph",
        discovered_models={
            "lan-mini": [
                {
                    "id": "fast-json",
                    "source": "lan-mini",
                    "provider_type": "local",
                    "tags": ["structured"],
                }
            ],
            "lan-dynamo": [
                {
                    "id": "reasoning-model",
                    "source": "lan-dynamo",
                    "provider_type": "local",
                    "tags": ["structured", "reasoning"],
                }
            ],
        },
    )

    monkeypatch.setattr("protoneo.llm.settings.load_settings", lambda: settings)

    model = resolve_paper_review_model(
        "ontology",
        require_local=True,
        phase_policy="fast_structured",
    )

    assert model == "lan-mini/fast-json"


def test_mini_only_graph_routing_ignores_disabled_dynamo(monkeypatch):
    settings = ProtoNeoSettings(
        lan_endpoints=[
            LocalEndpoint(
                id="lan-mini",
                display_name="Mini",
                url="http://mini/v1",
                location="lan",
                enabled=True,
            ),
            LocalEndpoint(
                id="lan-dynamo",
                display_name="Dynamo",
                url="http://dynamo/v1",
                location="lan",
                enabled=False,
            ),
        ],
        active_models={
            "lan-mini": "mini-reasoning",
            "lan-dynamo": "dynamo-reasoning",
        },
        presets=[
            ModelPreset(
                name="dynamo-preset",
                assignments={"ontology": "lan-dynamo/dynamo-reasoning"},
            )
        ],
        active_preset="dynamo-preset",
        discovered_models={
            "lan-mini": [
                {
                    "id": "mini-reasoning",
                    "source": "lan-mini",
                    "provider_type": "local",
                    "tags": ["structured", "reasoning"],
                }
            ],
            "lan-dynamo": [
                {
                    "id": "dynamo-reasoning",
                    "source": "lan-dynamo",
                    "provider_type": "local",
                    "tags": ["structured", "reasoning"],
                }
            ],
        },
    )

    monkeypatch.setattr("protoneo.llm.settings.load_settings", lambda: settings)

    model = resolve_paper_review_model(
        "ontology",
        require_local=True,
        phase_policy="fast_structured",
    )

    assert model == "lan-mini/mini-reasoning"


def test_graph_routing_does_not_switch_single_endpoint_to_discovered_model(monkeypatch):
    settings = ProtoNeoSettings(
        lan_endpoints=[
            LocalEndpoint(
                id="lan-mini",
                display_name="Mini",
                url="http://mini/v1",
                location="lan",
                enabled=True,
            ),
        ],
        active_models={
            "lan-mini": "mini-reasoning",
        },
        discovered_models={
            "lan-mini": [
                {
                    "id": "mini-reasoning",
                    "source": "lan-mini",
                    "provider_type": "local",
                    "tags": ["structured", "reasoning"],
                },
                {
                    "id": "mini-fast-json",
                    "source": "lan-mini",
                    "provider_type": "local",
                    "tags": ["structured"],
                },
                {
                    "id": "mini-loaded-json",
                    "source": "lan-mini",
                    "provider_type": "local",
                    "tags": ["structured"],
                    "loaded": True,
                },
                {
                    "id": "mini-mmproj-F16",
                    "source": "lan-mini",
                    "provider_type": "local",
                },
            ],
        },
    )

    monkeypatch.setattr("protoneo.llm.settings.load_settings", lambda: settings)

    model = resolve_paper_review_model(
        "ontology",
        require_local=True,
        phase_policy="fast_structured",
    )

    assert model == "lan-mini/mini-reasoning"


def test_step_state_normalizes_legacy_completed_status():
    assert StepState(status="completed").status == "complete"


def test_reviewer_provenance_includes_sampler_controls():
    cfg = _agent_config()
    output = AgentOutput(
        agent_id="technical_1",
        agent_role="Technical Reviewer",
        content='{"summary": "ok"}',
        metadata={"model": cfg.model},
    )

    review = parse_review_output(output, "technical", agent_config=cfg)

    assert review.provenance.top_k == 20
    assert review.provenance.min_p == 0.05
    assert review.provenance.repeat_penalty == 1.05
    assert review.provenance.reasoning_effort == "medium"


def test_session_to_review_packet_includes_parse_provenance():
    result = DeliberationResult(session_id="s1").model_dump(mode="json")
    session = SimpleNamespace(
        result={
            **result,
            "final_review": {"overall_merit": "borderline"},
        },
        config={
            "metadata": {"conference": "hpdc26", "paper_title": "Paper"},
            "agents": {},
        },
        knowledge_graph=None,
        app_data={"parse": {"parser": "docling", "figure_count": 2}},
    )

    packet = session_to_review_packet(session)

    assert packet.pc_chair_review["overall_merit"]["label"] == "borderline"
    assert packet.provenance_metadata["parse"]["parser"] == "docling"
    assert packet.provenance_metadata["parse"]["figure_count"] == 2


def test_review_route_aliases_are_registered():
    post_paths = {
        route.path
        for route in api.router.routes
        if "POST" in getattr(route, "methods", set())
    }

    assert "/review" in post_paths
    assert "/start-review" in post_paths
    assert "/sessions/upload" in post_paths


@pytest.mark.asyncio
async def test_concurrent_stream_usage_is_per_call():
    class FakeClient:
        def _strip_thinking(self, content):
            return content

        async def stream(self, *, model, usage_callback=None, **kwargs):
            if model == "model-a":
                yield "A"
                await asyncio.sleep(0.01)
                usage_callback({
                    "prompt_tokens": 1,
                    "completion_tokens": 2,
                    "total_tokens": 3,
                })
            else:
                yield "B"
                usage_callback({
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30,
                })

    client = FakeClient()
    agent_a = BaseAgent("A", "model-a", "Prompt.", client)
    agent_b = BaseAgent("B", "model-b", "Prompt.", client)

    response_a, response_b = await asyncio.gather(
        agent_a.process_stream(SessionContext("s1"), Message(role="user", content="go")),
        agent_b.process_stream(SessionContext("s2"), Message(role="user", content="go")),
    )

    assert response_a.metadata["usage"]["total_tokens"] == 3
    assert response_b.metadata["usage"]["total_tokens"] == 30
