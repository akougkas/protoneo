import json
from types import SimpleNamespace

import pytest

from protoneo.config.schema import AgentConfig, DeliberationConfig, PhaseConfig
from protoneo.deliberation.engine import DeliberationEngine
from protoneo.deliberation.session import SessionManager
from protoneo.llm.types import TokenUsage


class _CountingClient:
    def __init__(self):
        self.calls = []
        self.fail_models = set()

    async def complete(self, model, messages, **kwargs):
        self.calls.append(model)
        if model in self.fail_models:
            raise RuntimeError("provider unavailable")
        return SimpleNamespace(content=f"answer from {model}", usage=TokenUsage())

    def session_cost(self, session_id):
        return 0.0


@pytest.mark.asyncio
async def test_retry_with_persisted_configs_reuses_accepted_outputs(tmp_path):
    client = _CountingClient()
    sessions = SessionManager(tmp_path)
    engine = DeliberationEngine(client, sessions)
    agents = {aid: AgentConfig(role=aid, model=f"m/{aid}", system_prompt="review")
              for aid in ("a", "b", "meta")}
    config = DeliberationConfig(pattern="independent_synthesis", max_attempts=1, phases=[
        PhaseConfig(name="independent", mode="parallel", agents=["a", "b"]),
        PhaseConfig(name="synthesis", mode="sequential", agents=["meta"], input="all_prior_outputs"),
    ])
    session = await sessions.create(config={"agents": {k: v.model_dump() for k, v in agents.items()},
                                            "deliberation": config.model_dump()})

    client.fail_models = {"m/meta"}
    with pytest.raises(RuntimeError):
        await engine.run(session.session_id, agents, config, "paper", stream=False, resume=True)
    assert sorted(client.calls) == ["m/a", "m/b", "m/meta"]

    # A retry rebuilds configs from JSON, where int defaults come back as floats.
    stored = json.loads(json.dumps((await sessions.get(session.session_id)).config))
    reloaded_agents = {k: AgentConfig(**v) for k, v in stored["agents"].items()}
    reloaded_config = DeliberationConfig(**stored["deliberation"])
    client.calls.clear()
    client.fail_models = set()
    result = await engine.run(session.session_id, reloaded_agents, reloaded_config, "paper",
                              stream=False, resume=True)

    assert client.calls == ["m/meta"]
    assert [o.agent_id for p in result.phases for o in p.outputs] == ["a", "b", "meta"]
