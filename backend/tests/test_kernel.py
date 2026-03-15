"""
Tests for the ProtoNeo kernel foundation.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from litellm.exceptions import RateLimitError, ServiceUnavailableError, APIError

from protoneo.agents.base import BaseAgent
from protoneo.agents.types import AgentOutput, Document, Message
from protoneo.config.schema import AgentConfig, DeliberationConfig, PhaseConfig, ProtoNeoConfig
from protoneo.deliberation.engine import DeliberationEngine
from protoneo.deliberation.patterns import (
    IndependentSynthesisPattern,
    ParallelPattern,
    RoundRobinPattern,
    SequentialPattern,
)
from protoneo.deliberation.session import SessionContext, SessionManager
from protoneo.deliberation.types import DeliberationRules
from protoneo.knowledge.chunker import chunk_text
from protoneo.knowledge.parser import parse_file
from protoneo.llm.client import LLMClient
from protoneo.llm.registry import CapabilityRegistry
import protoneo.llm.settings as settings_module
from protoneo.llm.settings import LocalEndpoint, ProtoNeoSettings, active_model_assignments
from protoneo.llm.types import LLMResponse, ModelCapability, ModelInfo, TokenUsage


# ── Registry ────────────────────────────────────────────────

class TestCapabilityRegistry:
    def test_registry_loads_from_settings_snapshot(self):
        settings = ProtoNeoSettings(
            localhost_endpoints=[
                LocalEndpoint(
                    id="localhost-lmstudio",
                    display_name="LM Studio",
                    url="http://localhost:1234/v1",
                    location="localhost",
                )
            ],
            lan_endpoints=[
                LocalEndpoint(
                    id="lan-mini",
                    display_name="mini",
                    url="http://mini:8080/v1",
                    location="lan",
                )
            ],
            active_models={"lan-mini": "Qwen35-Distilled-i1-Q4_K_M"},
            discovered_models={
                "lan-mini": [
                    {
                        "id": "Qwen35-Distilled-i1-Q4_K_M",
                        "source": "lan-mini",
                        "provider_type": "local",
                        "context_length": 262144,
                    }
                ]
            },
            benchmark_results=[
                {
                    "provider": "lan-mini",
                    "model_id": "Qwen35-Distilled-i1-Q4_K_M",
                    "tags": ["structured", "reasoning"],
                    "throughput": {"tokens_per_second": 123.4},
                }
            ],
        )

        reg = CapabilityRegistry.from_settings(settings)
        info = reg.get("lan-mini/qwen35-distilled")

        assert len(reg) > 0
        assert info.provider == "lan-mini"
        assert info.effective_model == "openai/Qwen35-Distilled-i1-Q4_K_M"
        assert info.api_base == "http://mini:8080/v1"
        assert info.speed_tps == 123
        assert ModelCapability.STRUCTURED_OUTPUT in info.capabilities
        assert ModelCapability.EXTENDED_THINKING in info.capabilities

    def test_load_settings_migrates_legacy_endpoint_schema(self, tmp_path, monkeypatch):
        legacy_path = tmp_path / "settings.json"
        legacy_path.write_text(json.dumps({
            "local_endpoints": [
                {"name": "zbook", "url": "http://localhost:1234/v1", "type": "openai"},
                {"name": "ollama", "url": "http://localhost:11434", "type": "ollama"},
            ],
            "homelab_endpoints": [
                {"name": "mini", "url": "http://192.168.86.141:8080/v1", "type": "openai"},
                {"name": "dynamo", "url": "http://192.168.86.143:1234/v1", "type": "openai"},
            ],
            "provider_enabled": {"mini": False, "openrouter": False},
            "active_models": {
                "zbook": "lfm2-24b-a2b",
                "mini": "Qwen35-Distilled-i1-Q4_K_M",
            },
            "benchmark_results": [
                {"provider": "mini", "model_id": "Qwen35-Distilled-i1-Q4_K_M"},
            ],
            "discovered_models": {
                "local": [
                    {"id": "lfm2-24b-a2b", "source": "zbook"},
                ],
                "homelab": [
                    {"id": "Qwen35-Distilled-i1-Q4_K_M", "source": "mini"},
                ],
            },
        }))
        monkeypatch.setattr(settings_module, "_SETTINGS_DIR", tmp_path)
        monkeypatch.setattr(settings_module, "_SETTINGS_FILE", legacy_path)

        settings = settings_module.load_settings()

        assert [ep.id for ep in settings.localhost_endpoints] == ["localhost-lmstudio", "localhost-ollama"]
        assert [ep.display_name for ep in settings.localhost_endpoints] == ["LM Studio", "Ollama"]
        assert [ep.id for ep in settings.lan_endpoints] == ["lan-mini", "lan-dynamo"]
        assert settings.active_models["localhost-lmstudio"] == "lfm2-24b-a2b"
        assert settings.active_models["lan-mini"] == "Qwen35-Distilled-i1-Q4_K_M"
        assert settings.provider_enabled["openrouter"] is False
        assert settings.discovered_models["localhost-lmstudio"][0]["source"] == "localhost-lmstudio"
        assert settings.discovered_models["lan-mini"][0]["source"] == "lan-mini"
        assert settings.benchmark_results[0]["provider"] == "lan-mini"
        assert next(ep for ep in settings.lan_endpoints if ep.id == "lan-mini").enabled is False

    def test_load_settings_writes_default_schema_when_missing(self, tmp_path, monkeypatch):
        settings_path = tmp_path / "settings.json"
        monkeypatch.setattr(settings_module, "_SETTINGS_DIR", tmp_path)
        monkeypatch.setattr(settings_module, "_SETTINGS_FILE", settings_path)

        settings = settings_module.load_settings()
        saved = json.loads(settings_path.read_text())

        assert settings_path.exists()
        assert [ep.id for ep in settings.localhost_endpoints] == ["localhost-lmstudio", "localhost-ollama"]
        assert settings.lan_endpoints == []  # No LAN endpoints by default
        assert "localhost_endpoints" in saved
        assert "local_endpoints" not in saved
        assert "homelab_endpoints" not in saved

    def test_registry_accepts_legacy_provider_aliases(self):
        settings = ProtoNeoSettings(
            lan_endpoints=[
                LocalEndpoint(
                    id="lan-mini",
                    display_name="mini",
                    url="http://mini:8080/v1",
                    location="lan",
                )
            ],
            discovered_models={
                "lan-mini": [
                    {
                        "id": "Qwen35-Distilled-i1-Q4_K_M",
                        "source": "lan-mini",
                        "provider_type": "local",
                    }
                ]
            },
        )

        reg = CapabilityRegistry.from_settings(settings)
        info = reg.get("mini/qwen35-distilled")

        assert info.provider == "lan-mini"
        assert info.model_id == "lan-mini/Qwen35-Distilled-i1-Q4_K_M"

    def test_register_custom_model(self):
        reg = CapabilityRegistry(load_builtins=False)
        model = ModelInfo(model_id="test/model", provider="test")
        reg.register(model)
        assert reg.get("test/model").provider == "test"

    def test_fallback_for_unknown_model(self):
        reg = CapabilityRegistry(load_builtins=False)
        info = reg.get("ollama/llama3:8b")
        # "ollama" maps to canonical provider via aliases
        assert info.provider in ("ollama", "localhost-ollama")
        assert info.model_id == "ollama/llama3:8b"

    def test_find_by_capability(self):
        reg = CapabilityRegistry()
        vision_models = reg.find({ModelCapability.VISION})
        assert all(ModelCapability.VISION in m.capabilities for m in vision_models)

    def test_find_by_multiple_capabilities(self):
        reg = CapabilityRegistry()
        results = reg.find({ModelCapability.VISION, ModelCapability.EXTENDED_THINKING})
        for m in results:
            assert ModelCapability.VISION in m.capabilities
            assert ModelCapability.EXTENDED_THINKING in m.capabilities


# ── LLM Client ──────────────────────────────────────────────

class TestLLMClient:
    @pytest.mark.asyncio
    async def test_build_kwargs_with_provider_auth(self):
        settings = ProtoNeoSettings(
            lan_endpoints=[
                LocalEndpoint(
                    id="lan-mini",
                    display_name="mini",
                    url="http://192.168.86.141:8080/v1",
                    location="lan",
                )
            ],
            discovered_models={
                "lan-mini": [
                    {
                        "id": "Qwen35-Distilled-i1-Q4_K_M",
                        "source": "lan-mini",
                        "provider_type": "local",
                    }
                ]
            },
        )
        reg = CapabilityRegistry.from_settings(settings)
        client = LLMClient(
            registry=reg,
            api_keys={"lan-mini": "sk-test"},
            base_urls={},
        )
        kwargs = await client._build_kwargs_async(
            "lan-mini/qwen35-distilled",
            [{"role": "user", "content": "hi"}],
        )
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["model"] == "openai/Qwen35-Distilled-i1-Q4_K_M"
        assert "192.168.86.141" in kwargs["api_base"]

    def test_strip_thinking(self):
        text = "<think>reasoning here</think>Final answer."
        assert LLMClient._strip_thinking(text) == "Final answer."

    def test_strip_thinking_multiline(self):
        text = "<think>\nline1\nline2\n</think>\nClean output."
        assert LLMClient._strip_thinking(text) == "Clean output."

    def test_session_cost_tracking(self):
        reg = CapabilityRegistry()
        client = LLMClient(registry=reg)
        client._session_costs["s1"] = 0.05
        assert client.session_cost("s1") == 0.05
        assert client.session_cost("nonexistent") == 0.0

    def test_from_config(self):
        from protoneo.config.schema import LLMProviderConfig
        config = ProtoNeoConfig(
            providers={"anthropic": LLMProviderConfig(api_key="sk-test", base_url=None)},
        )
        client = LLMClient.from_config(config)
        assert client._api_keys["anthropic"] == "sk-test"


class TestSettingsRouting:
    def test_active_model_assignments_skip_disabled_providers(self):
        settings = ProtoNeoSettings(
            localhost_endpoints=[],
            lan_endpoints=[
                LocalEndpoint(
                    id="lan-mini",
                    display_name="mini",
                    url="http://mini:8080/v1",
                    location="lan",
                )
            ],
            provider_enabled={"openrouter": False},
            active_models={
                "lan-mini": "Qwen35-Distilled-i1-Q4_K_M",
                "openrouter": "nvidia/nemotron-3-super-120b-a12b:free",
            },
            discovered_models={
                "lan-mini": [
                    {
                        "id": "Qwen35-Distilled-i1-Q4_K_M",
                        "source": "lan-mini",
                        "provider_type": "local",
                    }
                ],
                "openrouter": [
                    {
                        "id": "nvidia/nemotron-3-super-120b-a12b:free",
                        "source": "openrouter",
                        "provider_type": "api",
                    }
                ],
            },
        )
        provider_registry = MagicMock()
        provider_registry.resolve_credential_info.return_value = {
            "api_key_source": "env",
        }

        assignments = active_model_assignments(settings=settings, provider_registry=provider_registry)

        assert set(assignments) == {"lan-mini"}
        assert assignments["lan-mini"]["model_id"] == "Qwen35-Distilled-i1-Q4_K_M"
        assert assignments["lan-mini"]["litellm_model"] == "openai/Qwen35-Distilled-i1-Q4_K_M"
        assert assignments["lan-mini"]["api_base"] == "http://mini:8080/v1"
        assert assignments["lan-mini"]["api_key_source"] == "local"


# ── Agent ───────────────────────────────────────────────────

class TestBaseAgent:
    def test_agent_properties(self):
        client = MagicMock(spec=LLMClient)
        agent = BaseAgent(
            role="Technical Reviewer",
            model="claude-opus-4-6",
            system_prompt="You are a reviewer.",
            llm_client=client,
            agent_id="tech_1",
        )
        assert agent.agent_id == "tech_1"
        assert agent.role == "Technical Reviewer"
        assert agent.model == "claude-opus-4-6"

    def test_build_messages(self):
        client = MagicMock(spec=LLMClient)
        agent = BaseAgent(
            role="Reviewer",
            model="test",
            system_prompt="Be helpful.",
            llm_client=client,
        )
        ctx = SessionContext("test-session")
        ctx.add_message(Message(role="assistant", content="Prior message"))

        msgs = agent._build_messages(
            ctx,
            Message(role="user", content="New input"),
        )
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "Be helpful."
        assert msgs[1]["content"] == "Prior message"
        assert msgs[2]["content"] == "New input"

    @pytest.mark.asyncio
    async def test_process(self):
        client = AsyncMock(spec=LLMClient)
        client.complete = AsyncMock(
            return_value=LLMResponse(
                content="I reviewed the paper.",
                model="test",
                usage=TokenUsage(),
            )
        )
        agent = BaseAgent(
            role="Reviewer",
            model="test",
            system_prompt="Review.",
            llm_client=client,
        )
        ctx = SessionContext("s1")
        msg = Message(role="user", content="Please review.")
        response = await agent.process(ctx, msg)
        assert response.content == "I reviewed the paper."
        assert response.agent_id == agent.agent_id

    @pytest.mark.asyncio
    async def test_review(self):
        client = AsyncMock(spec=LLMClient)
        client.complete = AsyncMock(
            return_value=LLMResponse(
                content="Strengths: good methodology.",
                model="test",
                usage=TokenUsage(),
            )
        )
        agent = BaseAgent(
            role="Reviewer",
            model="test",
            system_prompt="Review.",
            llm_client=client,
        )
        doc = Document(
            document_id="d1",
            filename="paper.pdf",
            text="This is a research paper.",
        )
        output = await agent.review(doc, session_id="s1")
        assert "methodology" in output.content
        assert output.agent_id == agent.agent_id


# ── Session ─────────────────────────────────────────────────

class TestSessionContext:
    def test_add_message(self):
        ctx = SessionContext("s1")
        ctx.add_message(Message(role="user", content="hello"))
        assert len(ctx.messages) == 1

    def test_add_output(self):
        ctx = SessionContext("s1")
        output = AgentOutput(agent_id="a1", agent_role="Reviewer", content="review")
        ctx.add_output(output)
        assert "a1" in ctx.agent_outputs
        assert len(ctx.agent_outputs["a1"]) == 1

    def test_add_document(self):
        ctx = SessionContext("s1")
        doc = Document(document_id="d1", filename="test.txt", text="content")
        ctx.add_document(doc)
        assert len(ctx.documents) == 1


class TestSessionManager:
    @pytest.mark.asyncio
    async def test_create_and_get(self, tmp_path):
        mgr = SessionManager(tmp_path)
        session = await mgr.create(config={"test": True})
        assert session.session_id

        retrieved = await mgr.get(session.session_id)
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, tmp_path):
        mgr = SessionManager(tmp_path)
        result = await mgr.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_sessions(self, tmp_path):
        mgr = SessionManager(tmp_path)
        await mgr.create()
        await mgr.create()
        sessions = await mgr.list_sessions()
        assert len(sessions) == 2


# ── Patterns ────────────────────────────────────────────────

def _make_mock_agent(agent_id: str, role: str, response_text: str) -> BaseAgent:
    """Create a BaseAgent with a mocked LLM client."""
    client = AsyncMock(spec=LLMClient)
    client.complete = AsyncMock(
        return_value=LLMResponse(
            content=response_text,
            model="test",
            usage=TokenUsage(),
        )
    )
    return BaseAgent(
        agent_id=agent_id,
        role=role,
        model="test",
        system_prompt=f"You are {role}.",
        llm_client=client,
    )


class TestSequentialPattern:
    @pytest.mark.asyncio
    async def test_sequential(self):
        a1 = _make_mock_agent("a1", "Analyst", "Analysis complete.")
        a2 = _make_mock_agent("a2", "Editor", "Edited output.")
        ctx = SessionContext("s1")
        msg = Message(role="user", content="Input data")
        rules = DeliberationRules()

        pattern = SequentialPattern()
        result = await pattern.execute([a1, a2], ctx, msg, rules)
        assert len(result.outputs) == 2
        assert result.outputs[0].agent_id == "a1"
        assert result.outputs[1].agent_id == "a2"


class TestParallelPattern:
    @pytest.mark.asyncio
    async def test_parallel(self):
        a1 = _make_mock_agent("a1", "Reviewer A", "Review A")
        a2 = _make_mock_agent("a2", "Reviewer B", "Review B")
        ctx = SessionContext("s1")
        msg = Message(role="user", content="Paper text")
        rules = DeliberationRules()

        pattern = ParallelPattern()
        result = await pattern.execute([a1, a2], ctx, msg, rules)
        assert len(result.outputs) == 2
        agent_ids = {o.agent_id for o in result.outputs}
        assert agent_ids == {"a1", "a2"}


class TestRoundRobinPattern:
    @pytest.mark.asyncio
    async def test_round_robin(self):
        a1 = _make_mock_agent("a1", "Debater A", "Point A")
        a2 = _make_mock_agent("a2", "Debater B", "Point B")
        ctx = SessionContext("s1")
        rules = DeliberationRules(max_rounds=2)

        pattern = RoundRobinPattern()
        result = await pattern.execute([a1, a2], ctx, rules)
        # 2 agents * 2 rounds = 4 outputs
        assert len(result.outputs) == 4


class TestIndependentSynthesisPattern:
    @pytest.mark.asyncio
    async def test_full_flow(self):
        r1 = _make_mock_agent("r1", "Technical", "Tech review")
        r2 = _make_mock_agent("r2", "Novelty", "Novelty review")
        synth = _make_mock_agent("meta", "Meta-Reviewer", "Final synthesis")
        ctx = SessionContext("s1")
        msg = Message(role="user", content="Paper content")
        rules = DeliberationRules(max_rounds=1)

        pattern = IndependentSynthesisPattern()
        result = await pattern.execute([r1, r2], synth, ctx, msg, rules)

        assert len(result.phases) == 3
        assert result.phases[0].phase_name == "independent_review"
        assert result.phases[1].phase_name == "deliberation"
        assert result.phases[2].phase_name == "meta_review"
        assert result.final_output is not None
        assert result.final_output.content == "Final synthesis"


# ── Knowledge ───────────────────────────────────────────────

class TestChunker:
    def test_short_text_single_chunk(self):
        chunks = chunk_text("Short text.", chunk_size=100)
        assert chunks == ["Short text."]

    def test_empty_text(self):
        chunks = chunk_text("")
        assert chunks == []

    def test_long_text_splits(self):
        text = "A" * 5000
        chunks = chunk_text(text, chunk_size=1000, overlap=100)
        assert len(chunks) > 1
        # Verify overlap: end of chunk N should overlap with start of chunk N+1
        for i in range(len(chunks) - 1):
            assert len(chunks[i]) > 0


# ── Config ──────────────────────────────────────────────────

class TestConfig:
    def test_from_env_with_no_keys(self):
        config = ProtoNeoConfig()
        assert config.providers == {}  # No providers without env vars

    def test_agent_config(self):
        cfg = AgentConfig(
            role="Reviewer",
            model="claude-opus-4-6",
            system_prompt="Review papers.",
            focus="methodology",
        )
        assert cfg.role == "Reviewer"
        assert cfg.focus == "methodology"

    def test_deliberation_config(self):
        cfg = DeliberationConfig(
            pattern="independent_synthesis",
            phases=[
                PhaseConfig(name="review", mode="parallel", agents=["r1", "r2"]),
                PhaseConfig(name="discuss", mode="round_robin", agents=["r1", "r2"], max_rounds=2),
                PhaseConfig(name="synthesize", mode="sequential", agents=["meta"]),
            ],
        )
        assert len(cfg.phases) == 3
        assert cfg.phases[1].max_rounds == 2


# ── SessionEventBus ────────────────────────────────────────

class TestSessionEventBus:
    def test_emit_and_subscribe(self):
        from protoneo.api.app import SessionEventBus
        bus = SessionEventBus()

        q = bus.subscribe()
        bus.emit("phase_start", {"phase": "review"})
        assert not q.empty()
        event = q.get_nowait()
        assert event["type"] == "phase_start"
        assert event["phase"] == "review"

    def test_replay_buffered_events(self):
        from protoneo.api.app import SessionEventBus
        bus = SessionEventBus()

        bus.emit("phase_start", {"phase": "review"})
        bus.emit("agent_start", {"agent_id": "tech"})

        # Late subscriber gets both buffered events
        q = bus.subscribe()
        assert q.qsize() == 2
        assert q.get_nowait()["type"] == "phase_start"
        assert q.get_nowait()["type"] == "agent_start"

    def test_multiple_subscribers(self):
        from protoneo.api.app import SessionEventBus
        bus = SessionEventBus()

        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.emit("test_event", {"data": "hello"})
        assert q1.qsize() == 1
        assert q2.qsize() == 1

    def test_unsubscribe(self):
        from protoneo.api.app import SessionEventBus
        bus = SessionEventBus()

        q = bus.subscribe()
        bus.unsubscribe(q)
        bus.emit("test_event", {"data": "hello"})
        assert q.empty()

    def test_unsubscribe_idempotent(self):
        from protoneo.api.app import SessionEventBus
        bus = SessionEventBus()

        q = bus.subscribe()
        bus.unsubscribe(q)
        bus.unsubscribe(q)  # second call should not raise

    def test_finished_property(self):
        from protoneo.api.app import SessionEventBus
        bus = SessionEventBus()

        assert not bus.finished
        bus.emit("phase_start", {"phase": "review"})
        assert not bus.finished
        bus.emit("completed", {"result": {}})
        assert bus.finished

    def test_finished_on_error(self):
        from protoneo.api.app import SessionEventBus
        bus = SessionEventBus()

        bus.emit("error", {"detail": "boom"})
        assert bus.finished


# ── LLMClient Retry ───────────────────────────────────────

class TestLLMClientRetry:
    @pytest.mark.asyncio
    async def test_retry_on_rate_limit(self):
        """Retries on RateLimitError and succeeds on second attempt."""
        reg = CapabilityRegistry(load_builtins=False)
        reg.register(ModelInfo(model_id="test/model", provider="test"))
        client = LLMClient(registry=reg, api_keys={"test": "sk-test"})

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "success"
        mock_response.choices[0].message.reasoning_content = None
        mock_response.usage = None
        mock_response.model_dump = MagicMock(return_value={})

        with patch("protoneo.llm.client.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [
                RateLimitError("rate limited", llm_provider="test", model="test/model", response=MagicMock(status_code=429)),
                mock_response,
            ]
            with patch("protoneo.llm.client._BASE_DELAY", 0.01):
                result = await client.complete("test/model", [{"role": "user", "content": "hi"}])
            assert result.content == "success"
            assert mock_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self):
        """After max retries, the error propagates."""
        reg = CapabilityRegistry(load_builtins=False)
        reg.register(ModelInfo(model_id="test/model", provider="test"))
        client = LLMClient(registry=reg, api_keys={"test": "sk-test"})

        with patch("protoneo.llm.client.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = ServiceUnavailableError(
                "unavailable", llm_provider="test", model="test/model", response=MagicMock(status_code=503)
            )
            with patch("protoneo.llm.client._BASE_DELAY", 0.01):
                with pytest.raises(ServiceUnavailableError):
                    await client.complete("test/model", [{"role": "user", "content": "hi"}], max_retries=2)
            assert mock_llm.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_5xx_api_error(self):
        """APIError with 5xx status code triggers retry."""
        reg = CapabilityRegistry(load_builtins=False)
        reg.register(ModelInfo(model_id="test/model", provider="test"))
        client = LLMClient(registry=reg, api_keys={"test": "sk-test"})

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "recovered"
        mock_response.choices[0].message.reasoning_content = None
        mock_response.usage = None
        mock_response.model_dump = MagicMock(return_value={})

        err = APIError(message="server error", status_code=502, llm_provider="openrouter", model="test/model")

        with patch("protoneo.llm.client.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = [err, mock_response]
            with patch("protoneo.llm.client._BASE_DELAY", 0.01):
                result = await client.complete("test/model", [{"role": "user", "content": "hi"}])
            assert result.content == "recovered"

    @pytest.mark.asyncio
    async def test_no_retry_on_4xx_api_error(self):
        """APIError with 4xx status code (bad request) is not retried."""
        reg = CapabilityRegistry(load_builtins=False)
        reg.register(ModelInfo(model_id="test/model", provider="test"))
        client = LLMClient(registry=reg, api_keys={"test": "sk-test"})

        err = APIError(message="bad request", status_code=400, llm_provider="test", model="test/model")

        with patch("protoneo.llm.client.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = err
            with pytest.raises(APIError):
                await client.complete("test/model", [{"role": "user", "content": "hi"}])
            assert mock_llm.call_count == 1


# ── Pattern Resilience ────────────────────────────────────

def _make_failing_agent(agent_id: str, role: str, error: Exception) -> BaseAgent:
    """Create a BaseAgent whose process() raises an error."""
    client = AsyncMock(spec=LLMClient)
    client.complete = AsyncMock(side_effect=error)
    return BaseAgent(
        agent_id=agent_id,
        role=role,
        model="test",
        system_prompt=f"You are {role}.",
        llm_client=client,
    )


class TestParallelPatternResilience:
    @pytest.mark.asyncio
    async def test_partial_failure_continues(self):
        """If one agent fails, the other's output is still collected."""
        good = _make_mock_agent("good", "Reviewer A", "Review A")
        bad = _make_failing_agent("bad", "Reviewer B", RuntimeError("LLM exploded"))
        ctx = SessionContext("s1")
        msg = Message(role="user", content="Paper")
        rules = DeliberationRules()

        pattern = ParallelPattern()
        result = await pattern.execute([good, bad], ctx, msg, rules)
        assert len(result.outputs) == 1
        assert result.outputs[0].agent_id == "good"

    @pytest.mark.asyncio
    async def test_all_fail_returns_empty(self):
        """If all agents fail, phase completes with zero outputs."""
        bad1 = _make_failing_agent("b1", "Reviewer A", RuntimeError("fail"))
        bad2 = _make_failing_agent("b2", "Reviewer B", RuntimeError("fail"))
        ctx = SessionContext("s1")
        msg = Message(role="user", content="Paper")
        rules = DeliberationRules()

        pattern = ParallelPattern()
        result = await pattern.execute([bad1, bad2], ctx, msg, rules)
        assert len(result.outputs) == 0

    @pytest.mark.asyncio
    async def test_failure_emits_agent_error_event(self):
        """Failed agents trigger an agent_error event."""
        bad = _make_failing_agent("bad", "Reviewer", RuntimeError("boom"))
        ctx = SessionContext("s1")
        msg = Message(role="user", content="Paper")
        rules = DeliberationRules()
        events = []

        pattern = ParallelPattern()
        await pattern.execute([bad], ctx, msg, rules, on_event=lambda t, d: events.append((t, d)))
        error_events = [e for e in events if e[0] == "agent_error"]
        assert len(error_events) == 1
        assert error_events[0][1]["agent_id"] == "bad"
        assert "boom" in error_events[0][1]["error"]


class TestRoundRobinResilience:
    @pytest.mark.asyncio
    async def test_one_agent_fails_others_continue(self):
        """If one agent fails during round-robin, the round continues with others."""
        good = _make_mock_agent("good", "Debater A", "My point")
        bad = _make_failing_agent("bad", "Debater B", RuntimeError("LLM crash"))
        ctx = SessionContext("s1")
        rules = DeliberationRules(max_rounds=1)

        pattern = RoundRobinPattern()
        result = await pattern.execute([good, bad], ctx, rules)
        # Only good agent produces output (1 round × 1 successful agent)
        assert len(result.outputs) == 1
        assert result.outputs[0].agent_id == "good"

    @pytest.mark.asyncio
    async def test_failure_emits_agent_error_event(self):
        """Failed agents in round-robin trigger agent_error events."""
        bad = _make_failing_agent("bad", "Debater", RuntimeError("crash"))
        ctx = SessionContext("s1")
        rules = DeliberationRules(max_rounds=1)
        events = []

        pattern = RoundRobinPattern()
        await pattern.execute([bad], ctx, rules, on_event=lambda t, d: events.append((t, d)))
        error_events = [e for e in events if e[0] == "agent_error"]
        assert len(error_events) == 1
        assert error_events[0][1]["round"] == 1


class TestIndependentSynthesisResilience:
    @pytest.mark.asyncio
    async def test_all_reviewers_fail_aborts(self):
        """If all reviewers fail in Phase 1, deliberation aborts with metadata."""
        bad1 = _make_failing_agent("b1", "Tech", RuntimeError("fail"))
        bad2 = _make_failing_agent("b2", "Novelty", RuntimeError("fail"))
        synth = _make_mock_agent("meta", "Meta-Reviewer", "Synthesis")
        ctx = SessionContext("s1")
        msg = Message(role="user", content="Paper")
        rules = DeliberationRules(max_rounds=1)

        pattern = IndependentSynthesisPattern()
        result = await pattern.execute([bad1, bad2], synth, ctx, msg, rules)
        assert result.final_output is None
        assert result.metadata.get("aborted") is True
        assert result.metadata.get("reason") == "all_reviewers_failed"
        # Only Phase 1 ran
        assert len(result.phases) == 1

    @pytest.mark.asyncio
    async def test_partial_failure_still_completes(self):
        """If some reviewers fail, deliberation continues with partial reviews."""
        good = _make_mock_agent("r1", "Technical", "Tech review")
        bad = _make_failing_agent("r2", "Novelty", RuntimeError("fail"))
        synth = _make_mock_agent("meta", "Meta-Reviewer", "Synthesis")
        ctx = SessionContext("s1")
        msg = Message(role="user", content="Paper")
        rules = DeliberationRules(max_rounds=1)

        pattern = IndependentSynthesisPattern()
        result = await pattern.execute([good, bad], synth, ctx, msg, rules)
        # All 3 phases complete
        assert len(result.phases) == 3
        assert result.final_output is not None
        assert result.final_output.content == "Synthesis"
        # Phase 1 only has 1 output
        assert len(result.phases[0].outputs) == 1


# ── Token Streaming ──────────────────────────────────────

class TestTokenStreaming:
    """Tests for token-level streaming through patterns."""

    def _make_streaming_agent(self, role="tester", content="Hello streaming world"):
        """Create a mock agent with process_stream that yields chunks."""
        agent = MagicMock()
        agent.agent_id = f"{role}_001"
        agent.role = role

        chunks = list(content)  # split into individual chars

        async def mock_process_stream(ctx, msg, on_token=None, include_history=True, **kwargs):
            for chunk in chunks:
                if on_token:
                    on_token(chunk)
                await asyncio.sleep(0)
            return Message(role="assistant", content=content, agent_id=agent.agent_id)

        async def mock_process(ctx, msg, include_history=True, **kwargs):
            return Message(role="assistant", content=content, agent_id=agent.agent_id)

        agent.process_stream = AsyncMock(side_effect=mock_process_stream)
        agent.process = AsyncMock(side_effect=mock_process)
        return agent

    @pytest.mark.asyncio
    async def test_sequential_streams_tokens(self):
        agent = self._make_streaming_agent("writer", "abc")
        context = SessionContext(session_id="stream-1")
        msg = Message(role="user", content="go")
        rules = DeliberationRules()

        received_tokens = []
        def on_event(evt_type, data):
            if evt_type == "token":
                received_tokens.append(data["chunk"])

        pattern = SequentialPattern()
        result = await pattern.execute([agent], context, msg, rules, on_event=on_event, stream=True)

        assert result.outputs[0].content == "abc"
        assert "".join(received_tokens) == "abc"

    @pytest.mark.asyncio
    async def test_parallel_streams_tokens_per_agent(self):
        a1 = self._make_streaming_agent("alpha", "AAA")
        a2 = self._make_streaming_agent("beta", "BBB")
        context = SessionContext(session_id="stream-2")
        msg = Message(role="user", content="go")
        rules = DeliberationRules()

        token_log = []
        def on_event(evt_type, data):
            if evt_type == "token":
                token_log.append((data["agent_id"], data["chunk"]))

        pattern = ParallelPattern()
        result = await pattern.execute([a1, a2], context, msg, rules, on_event=on_event, stream=True)

        assert len(result.outputs) == 2
        alpha_tokens = "".join(c for aid, c in token_log if aid == "alpha_001")
        beta_tokens = "".join(c for aid, c in token_log if aid == "beta_001")
        assert alpha_tokens == "AAA"
        assert beta_tokens == "BBB"

    @pytest.mark.asyncio
    async def test_stream_false_uses_regular_process(self):
        """When stream=False, patterns call process() not process_stream()."""
        agent = self._make_streaming_agent("agent", "xyz")
        context = SessionContext(session_id="stream-3")
        msg = Message(role="user", content="go")
        rules = DeliberationRules()

        pattern = SequentialPattern()
        result = await pattern.execute([agent], context, msg, rules, on_event=None, stream=False)

        assert result.outputs[0].content == "xyz"
        agent.process.assert_called_once()
        agent.process_stream.assert_not_called()


# ── Knowledge Graph Extraction ────────────────────────────

class TestGraphExtraction:
    """Tests for the LLM-based knowledge graph extractor."""

    def test_parse_valid_json(self):
        from protoneo.knowledge.graph_extractor import _parse_extraction
        raw = json.dumps({
            "entities": [
                {"name": "Transformer", "type": "Method", "description": "attention model"}
            ],
            "relationships": [
                {"source": "Transformer", "target": "ImageNet", "type": "EVALUATES_ON", "description": ""}
            ],
        })
        result = _parse_extraction(raw)
        assert len(result.entities) == 1
        assert result.entities[0].name == "Transformer"
        assert len(result.relationships) == 1

    def test_parse_fenced_json(self):
        from protoneo.knowledge.graph_extractor import _parse_extraction
        raw = "Here is the graph:\n```json\n" + json.dumps({
            "entities": [{"name": "CNN", "type": "Method", "description": ""}],
            "relationships": [],
        }) + "\n```\nDone."
        result = _parse_extraction(raw)
        assert len(result.entities) == 1

    def test_parse_invalid_returns_empty(self):
        from protoneo.knowledge.graph_extractor import _parse_extraction
        result = _parse_extraction("This is not JSON at all.")
        assert len(result.entities) == 0
        assert len(result.relationships) == 0

    def test_extracted_to_graph_data(self):
        from protoneo.knowledge.graph_extractor import ExtractedGraph, GraphEntity, GraphRelationship, extracted_to_graph_data
        extracted = ExtractedGraph(
            entities=[
                GraphEntity(name="ResNet", type="Method", description="deep residual network"),
                GraphEntity(name="CIFAR-10", type="Dataset", description="image classification benchmark"),
            ],
            relationships=[
                GraphRelationship(source="ResNet", target="CIFAR-10", type="EVALUATES_ON"),
            ],
        )
        data = extracted_to_graph_data(extracted)
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1
        assert data["nodes"][0]["labels"] == ["Entity", "Method"]
        assert data["edges"][0]["name"] == "EVALUATES_ON"

    def test_missing_entity_in_relationship_skipped(self):
        from protoneo.knowledge.graph_extractor import ExtractedGraph, GraphEntity, GraphRelationship, extracted_to_graph_data
        extracted = ExtractedGraph(
            entities=[GraphEntity(name="A", type="Method")],
            relationships=[
                GraphRelationship(source="A", target="NonExistent", type="USES"),
            ],
        )
        data = extracted_to_graph_data(extracted)
        assert len(data["edges"]) == 0


# ── Paper Ontology Generation ─────────────────────────────

class TestPaperOntology:
    """Tests for the paper-specific ontology generator."""

    def test_parse_valid_ontology(self):
        from protoneo.knowledge.paper_ontology import _parse_ontology
        raw = json.dumps({
            "entity_types": [
                {"name": "Method", "description": "Techniques and algorithms", "attributes": [], "examples": ["CNN"]}
            ],
            "edge_types": [
                {"name": "USES", "description": "Method uses something", "source_targets": [{"source": "Method", "target": "Dataset"}]}
            ],
            "analysis_summary": "An ML paper",
            "paper_domain": "machine learning",
            "key_contributions": ["New architecture"],
        })
        ontology = _parse_ontology(raw)
        assert len(ontology.entity_types) == 1
        assert ontology.paper_domain == "machine learning"
        assert len(ontology.key_contributions) == 1

    def test_validate_adds_fallbacks(self):
        from protoneo.knowledge.paper_ontology import PaperOntology, OntologyEntityType, _validate_ontology
        ontology = PaperOntology(
            entity_types=[
                OntologyEntityType(name=f"Type{i}", description=f"Type {i} desc") for i in range(5)
            ],
        )
        validated = _validate_ontology(ontology)
        # 5 LLM-specific + 7 base + 2 fallback + 1 structural = 15
        assert len(validated.entity_types) == 15
        names = [et.name for et in validated.entity_types]
        assert "Concept" in names
        assert "Reference" in names
        assert "Claim" in names
        assert "Method" in names
        assert "Equation" in names

    def test_validate_caps_at_fifteen(self):
        from protoneo.knowledge.paper_ontology import PaperOntology, OntologyEntityType, _validate_ontology
        ontology = PaperOntology(
            entity_types=[
                OntologyEntityType(name=f"Type{i}", description=f"Desc") for i in range(15)
            ],
        )
        validated = _validate_ontology(ontology)
        # Max 5 LLM types + 10 base/fallback/structural = 15
        assert len(validated.entity_types) == 15

    def test_validate_truncates_descriptions(self):
        from protoneo.knowledge.paper_ontology import PaperOntology, OntologyEntityType, _validate_ontology
        ontology = PaperOntology(
            entity_types=[
                OntologyEntityType(name="LongDesc", description="x" * 200),
            ],
        )
        validated = _validate_ontology(ontology)
        for et in validated.entity_types:
            assert len(et.description) <= 100

    def test_ontology_to_extraction_prompt(self):
        from protoneo.knowledge.paper_ontology import PaperOntology, OntologyEntityType, OntologyEdgeType, OntologyAttribute, ontology_to_extraction_prompt
        ontology = PaperOntology(
            entity_types=[
                OntologyEntityType(
                    name="Method",
                    description="Algorithms and techniques",
                    attributes=[OntologyAttribute(name="complexity", description="Time complexity")],
                    examples=["SGD", "Adam"],
                ),
            ],
            edge_types=[
                OntologyEdgeType(
                    name="EVALUATES_ON",
                    description="Method evaluated on dataset",
                    source_targets=[{"source": "Method", "target": "Dataset"}],
                ),
            ],
            paper_domain="optimization",
            key_contributions=["New optimizer"],
        )
        prompt = ontology_to_extraction_prompt(ontology)
        assert "Method" in prompt
        assert "EVALUATES_ON" in prompt
        assert "optimization" in prompt
        assert "complexity" in prompt


# ── Pipeline Control ──────────────────────────────────────

class TestPipelineControl:
    """Tests for the generalized pipeline control system."""

    def test_auto_advance_does_not_block(self):
        """In auto mode, wait_if_paused returns immediately."""
        from protoneo.api.app import PipelineControl
        ctl = PipelineControl()
        assert ctl.auto_advance is True
        ctl.enter_stage("pre_review")
        ctl.enter_step("ontology")
        assert not ctl.paused

    @pytest.mark.asyncio
    async def test_auto_advance_wait(self):
        from protoneo.api.app import PipelineControl
        ctl = PipelineControl()
        ctl.enter_stage("pre_review")
        # Should not block in auto mode
        await asyncio.wait_for(ctl.wait_if_paused(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_manual_mode_blocks_until_advance(self):
        from protoneo.api.app import PipelineControl
        ctl = PipelineControl()
        # Simulate the mandatory gate between pre_review and review
        advanced = False
        async def wait_then_advance():
            nonlocal advanced
            await asyncio.sleep(0.05)
            ctl.advance()
            advanced = True

        asyncio.create_task(wait_then_advance())
        await asyncio.wait_for(ctl.wait_for_gate(), timeout=1.0)
        assert advanced

    def test_pause_switches_to_manual(self):
        from protoneo.api.app import PipelineControl
        ctl = PipelineControl()
        assert ctl.auto_advance is True
        ctl.pause()
        assert ctl.auto_advance is False
        assert ctl.paused is True

    def test_resume_switches_to_auto(self):
        from protoneo.api.app import PipelineControl
        ctl = PipelineControl()
        ctl.pause()
        ctl.resume()
        assert ctl.auto_advance is True
        assert ctl.paused is False

    def test_stage_tracking(self):
        from protoneo.api.app import PipelineControl
        ctl = PipelineControl()
        ctl.enter_stage("pre_review")
        ctl.enter_step("ontology")
        ctl.stage_done("pre_review")
        ctl.enter_stage("review")
        ctl.stage_done("review")
        assert ctl.completed_stages == ["pre_review", "review"]
        assert ctl.current_stage == "review"

    def test_status_dict(self):
        from protoneo.api.app import PipelineControl
        ctl = PipelineControl()
        ctl.enter_stage("pre_review")
        ctl.enter_step("ontology")
        s = ctl.status()
        assert s["current_stage"] == "pre_review"
        assert s["current_step"] == "ontology"
        assert s["auto_advance"] is True
        assert isinstance(s["completed_stages"], list)


# ── Pipeline Feature Tests ────────────────────────────────────


class TestStepState:
    def test_default_values(self):
        from protoneo.deliberation.session import StepState
        s = StepState()
        assert s.status == "pending"
        assert s.started_at is None
        assert s.nodes_added == 0

    def test_custom_values(self):
        from protoneo.deliberation.session import StepState
        s = StepState(status="complete", model_used="dynamo/qwen", nodes_added=42)
        assert s.status == "complete"
        assert s.model_used == "dynamo/qwen"
        assert s.nodes_added == 42


class TestSessionPipelineFields:
    def test_session_has_pipeline_fields(self):
        from protoneo.deliberation.session import Session
        s = Session()
        assert s.pipeline_steps == {}
        assert s.graph_after_step == {}

    def test_session_serializes_pipeline_steps(self):
        from protoneo.deliberation.session import Session, StepState
        s = Session()
        s.pipeline_steps["ontology"] = StepState(status="complete", model_used="test").model_dump()
        data = s.model_dump(mode="json")
        assert data["pipeline_steps"]["ontology"]["status"] == "complete"

    def test_session_serializes_graph_snapshots(self):
        from protoneo.deliberation.session import Session
        from protoneo.knowledge.paper_graph import PaperGraph
        s = Session()
        pg = PaperGraph()
        pg.add_node("TestEntity", "Method")
        s.graph_after_step["extract"] = pg.snapshot()
        data = s.model_dump(mode="json")
        assert len(data["graph_after_step"]["extract"]["nodes"]) == 1


class TestBaseOntology:
    def test_base_entity_types_count(self):
        from protoneo.knowledge.paper_ontology import _BASE_ENTITY_TYPES
        assert len(_BASE_ENTITY_TYPES) == 7
        names = {t.name for t in _BASE_ENTITY_TYPES}
        assert names == {"Claim", "Method", "Dataset", "Metric", "Baseline", "Result", "Limitation"}

    def test_fallback_entity_types(self):
        from protoneo.knowledge.paper_ontology import _FALLBACK_ENTITY_TYPES
        assert len(_FALLBACK_ENTITY_TYPES) == 2
        names = {t.name for t in _FALLBACK_ENTITY_TYPES}
        assert names == {"Concept", "Reference"}

    def test_structural_entity_types(self):
        from protoneo.knowledge.paper_ontology import _STRUCTURAL_ENTITY_TYPES
        assert len(_STRUCTURAL_ENTITY_TYPES) == 1
        assert _STRUCTURAL_ENTITY_TYPES[0].name == "Equation"

    def test_base_edge_types(self):
        from protoneo.knowledge.paper_ontology import _BASE_EDGE_TYPES
        assert len(_BASE_EDGE_TYPES) == 8
        names = {t.name for t in _BASE_EDGE_TYPES}
        expected = {"USES", "EVALUATES_ON", "COMPARED_AGAINST", "ACHIEVES", "EXTENDS", "CITES", "PART_OF", "CONTRADICTS"}
        assert names == expected

    def test_structural_edge_types(self):
        from protoneo.knowledge.paper_ontology import _STRUCTURAL_EDGE_TYPES
        assert len(_STRUCTURAL_EDGE_TYPES) == 4
        names = {t.name for t in _STRUCTURAL_EDGE_TYPES}
        expected = {"HAS_SECTION", "CONTAINS", "APPEARS_IN", "ALIAS_OF"}
        assert names == expected

    def test_validate_deduplicates_base_types(self):
        from protoneo.knowledge.paper_ontology import PaperOntology, OntologyEntityType, _validate_ontology
        # LLM returns "Method" which overlaps with base type
        ontology = PaperOntology(
            entity_types=[
                OntologyEntityType(name="Method", description="duplicate"),
                OntologyEntityType(name="CustomType", description="custom"),
            ],
        )
        validated = _validate_ontology(ontology)
        method_count = sum(1 for t in validated.entity_types if t.name == "Method")
        assert method_count == 1  # Only the base type, LLM duplicate removed

    def test_base_entity_attributes(self):
        from protoneo.knowledge.paper_ontology import _BASE_ENTITY_TYPES
        claim = next(t for t in _BASE_ENTITY_TYPES if t.name == "Claim")
        attr_names = {a.name for a in claim.attributes}
        assert "stated_in_section" in attr_names
        assert "evidence_strength" in attr_names
        assert "quantified" in attr_names


class TestNLPPrepass:
    def test_extract_citation_markers_bracket(self):
        from protoneo.knowledge.metadata import extract_citation_markers
        markers = extract_citation_markers("See [1] and [2-5] for details.")
        assert len(markers) >= 2
        types = {m["type"] for m in markers}
        assert "bracket" in types

    def test_extract_citation_markers_author_year(self):
        from protoneo.knowledge.metadata import extract_citation_markers
        markers = extract_citation_markers("According to (Smith, 2024) and (Jones et al., 2023).")
        assert len(markers) >= 2
        types = {m["type"] for m in markers}
        assert "author_year" in types

    def test_extract_equation_labels(self):
        from protoneo.knowledge.metadata import extract_equation_labels
        labels = extract_equation_labels("See Eq. 1 and Theorem 2. Also Lemma 3 is key.")
        assert len(labels) == 3
        assert "Eq. 1" in labels
        assert "Theorem 2" in labels
        assert "Lemma 3" in labels

    def test_metadata_has_new_fields(self):
        from protoneo.knowledge.metadata import extract_metadata
        meta = extract_metadata("Title\nAbstract\nWe cite [1]. Eq. 1 holds.\n1. Introduction\nText.\nReferences\n[1] A paper.")
        assert hasattr(meta, "citation_markers")
        assert hasattr(meta, "equation_labels")
        assert hasattr(meta, "section_texts")

    def test_section_texts_populated(self):
        from protoneo.knowledge.metadata import extract_metadata
        text = "1. Introduction\nSome text here.\n2. Methods\nMethod details.\n3. Results\nOur results."
        meta = extract_metadata(text)
        assert len(meta.section_texts) > 0


class TestPaperGraphNewMethods:
    def test_add_ontology_nodes(self):
        from protoneo.knowledge.paper_graph import PaperGraph
        from protoneo.knowledge.paper_ontology import PaperOntology
        pg = PaperGraph()
        pg.add_node("Test Paper", "Paper", node_id="paper-root")
        ontology = PaperOntology(key_contributions=["Novel sorting", "Hilbert pivots"])
        pg.add_ontology_nodes(ontology)
        assert pg.ontology is ontology
        labels = {n.label for n in pg.nodes}
        assert "Novel sorting" in labels
        assert "Hilbert pivots" in labels
        # Check PART_OF edges
        part_of_edges = [e for e in pg.edges if e.edge_type == "PART_OF"]
        assert len(part_of_edges) == 2

    def test_get_accumulated_context(self):
        from protoneo.knowledge.paper_graph import PaperGraph
        pg = PaperGraph()
        pg.add_node("ScaleSort", "Method", description="A novel sorting algorithm")
        pg.add_node("Frontier", "Dataset", description="Supercomputer benchmark")
        pg.add_node("Introduction", "Section")  # structural, should be skipped
        ctx = pg.get_accumulated_context()
        assert "ScaleSort" in ctx
        assert "Frontier" in ctx
        assert "Introduction" not in ctx  # structural filtered out

    def test_get_accumulated_context_max_tokens(self):
        from protoneo.knowledge.paper_graph import PaperGraph
        pg = PaperGraph()
        for i in range(100):
            pg.add_node(f"Entity{i}", "Method", description="A" * 60)
        ctx = pg.get_accumulated_context(max_tokens=100)
        # Should be truncated to roughly 400 chars (100 tokens * 4 chars/token)
        assert len(ctx) < 1000

    def test_snapshot_and_restore(self):
        from protoneo.knowledge.paper_graph import PaperGraph
        pg = PaperGraph()
        pg.add_node("TestNode", "Method", description="test")
        pg.add_edge("n1", "n2", "USES")
        snap = pg.snapshot()
        assert isinstance(snap, dict)
        assert "nodes" in snap
        pg2 = PaperGraph.restore_from_snapshot(snap)
        assert len(pg2.nodes) == len(pg.nodes)
        assert len(pg2.edges) == len(pg.edges)

    def test_ingest_metadata_equations(self):
        from protoneo.knowledge.paper_graph import PaperGraph
        from protoneo.knowledge.metadata import extract_metadata
        text = "Title\nAbstract\nWe use Eq. 1 and Theorem 2.\n1. Introduction\nText.\nReferences\n[1] A paper."
        meta = extract_metadata(text)
        pg = PaperGraph()
        pg.ingest_metadata(meta)
        eq_nodes = [n for n in pg.nodes if n.node_type == "Equation"]
        assert len(eq_nodes) == 2

    def test_ingest_metadata_references_as_attribute(self):
        from protoneo.knowledge.paper_graph import PaperGraph
        from protoneo.knowledge.metadata import extract_metadata
        text = "Title\nAbstract\nCitation [1].\n1. Intro\nText.\nReferences\n[1] Dean et al., MapReduce, 2004.\n[2] Smith, Sorting, 2020."
        meta = extract_metadata(text)
        pg = PaperGraph()
        pg.ingest_metadata(meta)
        # References are stored as attributes on paper root, not individual nodes
        root = pg.node_by_id("paper-root")
        assert root is not None
        assert "reference_sample" in root.attributes or meta.reference_count > 0


class TestCorefResolver:
    def test_parse_coref_response_valid(self):
        from protoneo.knowledge.coref_resolver import _parse_coref_response
        raw = '{"merges": [{"keep": "ScaleSort", "remove": ["our method"]}], "aliases": [{"full": "ScaleSort", "abbreviation": "SS"}]}'
        result = _parse_coref_response(raw)
        assert len(result["merges"]) == 1
        assert len(result["aliases"]) == 1

    def test_parse_coref_response_code_fence(self):
        from protoneo.knowledge.coref_resolver import _parse_coref_response
        raw = '```json\n{"merges": [], "aliases": []}\n```'
        result = _parse_coref_response(raw)
        assert result["merges"] == []

    def test_parse_coref_response_invalid(self):
        from protoneo.knowledge.coref_resolver import _parse_coref_response
        result = _parse_coref_response("not json at all")
        assert result == {"merges": [], "aliases": []}

    @pytest.mark.asyncio
    async def test_resolve_coreferences_small_graph(self):
        from protoneo.knowledge.coref_resolver import resolve_coreferences
        from protoneo.knowledge.paper_graph import PaperGraph
        pg = PaperGraph()
        pg.add_node("A", "Method")
        mock_client = AsyncMock()
        stats = await resolve_coreferences(pg, mock_client)
        # Too few entities, should skip LLM call
        assert stats["merged"] == 0
        assert stats["aliases_created"] == 0


class TestGraphVerifier:
    def test_parse_verification_valid(self):
        from protoneo.knowledge.graph_verifier import _parse_verification
        raw = '{"grounding_issues": [{"entity": "X", "issue": "not found"}], "missing_concepts": [], "missing_connections": []}'
        result = _parse_verification(raw)
        assert len(result["grounding_issues"]) == 1

    def test_parse_verification_invalid(self):
        from protoneo.knowledge.graph_verifier import _parse_verification
        result = _parse_verification("garbage")
        assert result == {"grounding_issues": [], "missing_concepts": [], "missing_connections": []}

    def test_verification_result_model(self):
        from protoneo.knowledge.graph_verifier import VerificationResult
        vr = VerificationResult()
        assert vr.entities_added == 0
        assert vr.entities_flagged == 0
        assert vr.grounding_issues == []

    @pytest.mark.asyncio
    async def test_verify_empty_graph(self):
        from protoneo.knowledge.graph_verifier import verify_graph
        from protoneo.knowledge.paper_graph import PaperGraph
        pg = PaperGraph()
        mock_client = AsyncMock()
        result = await verify_graph(pg, "some text", mock_client)
        assert result.entities_added == 0


class TestSectionAwareExtraction:
    def test_section_prompt_template_exists(self):
        from protoneo.knowledge.graph_extractor import _SECTION_PROMPT_TEMPLATE, _SECTION_SYSTEM
        assert "section" in _SECTION_SYSTEM.lower()
        assert "{section_name}" in _SECTION_PROMPT_TEMPLATE
        assert "{accumulated_context_block}" in _SECTION_PROMPT_TEMPLATE

    @pytest.mark.asyncio
    async def test_extract_with_paper_graph(self):
        """Section-aware extraction populates the PaperGraph directly."""
        from protoneo.knowledge.graph_extractor import extract_paper_graph
        from protoneo.knowledge.paper_graph import PaperGraph
        from protoneo.knowledge.paper_ontology import PaperOntology, OntologyEntityType

        pg = PaperGraph()
        pg.add_node("Test Paper", "Paper", node_id="paper-root")

        ontology = PaperOntology(
            entity_types=[OntologyEntityType(name="Method", description="test")],
        )

        mock_client = AsyncMock()
        mock_client.complete.return_value = LLMResponse(
            content='{"entities": [{"name": "ScaleSort", "type": "Method", "description": "sorting algo"}], "relationships": []}',
            model="test",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
        )

        result = await extract_paper_graph(
            text="1. Introduction\nScaleSort is a sorting algorithm.\n2. Methods\nWe use Hilbert curves.",
            llm_client=mock_client,
            model="test",
            ontology=ontology,
            paper_graph=pg,
        )

        # Graph should have been populated directly
        assert len(pg.nodes) > 1  # paper-root + extracted entities
        method_nodes = [n for n in pg.nodes if n.node_type == "Method"]
        assert len(method_nodes) >= 1

    @pytest.mark.asyncio
    async def test_extract_without_paper_graph_backward_compat(self):
        """Without paper_graph, falls back to chunk-based extraction."""
        from protoneo.knowledge.graph_extractor import extract_paper_graph

        mock_client = AsyncMock()
        mock_client.complete.return_value = LLMResponse(
            content='{"entities": [{"name": "TestMethod", "type": "Method", "description": "test"}], "relationships": []}',
            model="test",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=10, total_tokens=20),
        )

        result = await extract_paper_graph(
            text="Test paper text with enough content to extract.",
            llm_client=mock_client,
            model="test",
        )

        # Should return D3 format dict
        assert "nodes" in result
        assert "edges" in result


class TestPipelineStepEndpoints:
    @pytest.fixture
    def app_client(self):
        from protoneo.api.app import create_app
        from protoneo.config.schema import ProtoNeoConfig
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            config = ProtoNeoConfig()
            config.storage.session_dir = tmpdir
            app = create_app(config)
            from fastapi.testclient import TestClient
            yield TestClient(app)

    def test_pipeline_status_no_session(self, app_client):
        resp = app_client.get("/api/sessions/nonexistent/pipeline/status")
        assert resp.status_code == 404

    def test_step_run_invalid_step(self, app_client):
        resp = app_client.post("/api/sessions/test/pipeline/step/invalid_step/run")
        assert resp.status_code == 400
