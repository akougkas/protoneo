"""Durable phase execution for configurable multi-agent deliberation."""

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable

from ..agents.base import BaseAgent
from ..agents.types import Message
from ..config.schema import AgentConfig, DeliberationConfig
from ..llm.client import LLMClient
from ..llm.errors import sanitize_error_message
from .patterns import ParallelPattern, RoundRobinPattern, SequentialPattern
from .policy import DeliberationPolicy
from .session import SessionManager, SessionStatus
from .types import DeliberationResult, DeliberationRules, PhaseResult

logger = logging.getLogger("protoneo.deliberation.engine")
EventCallback = Callable[[str, dict], None] | None


class DeliberationEngine:
    def __init__(self, llm_client: LLMClient, session_manager: SessionManager):
        self.llm_client = llm_client
        self.session_manager = session_manager
        self._active: set[str] = set()

    def _create_agent(self, agent_id: str, config: AgentConfig) -> BaseAgent:
        fields = config.model_dump(exclude={"grounding"})
        return BaseAgent(agent_id=agent_id, llm_client=self.llm_client, **fields)

    @staticmethod
    def validate_config(agent_configs: dict[str, AgentConfig], config: DeliberationConfig) -> None:
        if not config.phases:
            raise ValueError("At least one deliberation phase is required")
        names = [p.name for p in config.phases]
        if len(names) != len(set(names)):
            raise ValueError("Deliberation phase names must be unique")
        for phase in config.phases:
            missing = set(phase.agents) - agent_configs.keys()
            if missing:
                raise ValueError(f"Phase '{phase.name}' references unknown agents: {', '.join(sorted(missing))}")
            unassigned = [aid for aid in phase.agents if not agent_configs[aid].model.strip()]
            if unassigned:
                raise ValueError(f"No model assigned to: {', '.join(unassigned)}. Configure models before starting deliberation")
            if len(phase.agents) != len(set(phase.agents)):
                raise ValueError(f"Phase '{phase.name}' contains duplicate agents")
            if phase.min_successful_agents > len(phase.agents):
                raise ValueError(f"Phase '{phase.name}' requires more successful agents than configured")
            if phase.input not in (None, "original", "previous_output", "all_prior_outputs"):
                raise ValueError(f"Unsupported input selector for '{phase.name}': {phase.input}")
        if config.pattern == "independent_synthesis":
            modes = [p.mode for p in config.phases]
            if modes[0] != "parallel" or modes[-1] != "sequential":
                raise ValueError("independent_synthesis requires independent participants and a final synthesizer")

    async def run(
        self, session_id: str, agent_configs: dict[str, AgentConfig],
        deliberation_config: DeliberationConfig, user_message: str,
        on_event: EventCallback = None, stream: bool | None = None, *,
        policy: DeliberationPolicy | None = None,
        before_turn: Callable[[], Awaitable[None]] | None = None,
        resume: bool = False, finalize_session: bool = True,
    ) -> DeliberationResult:
        self.validate_config(agent_configs, deliberation_config)
        if session_id in self._active:
            raise ValueError(f"Session {session_id} is already executing")
        started = time.monotonic()
        result = DeliberationResult(session_id=session_id)
        prior_duration = 0.0
        prior_cost = 0.0
        cost_at_start = self.llm_client.session_cost(session_id)
        prior_cost = cost_at_start
        session = None
        policy = policy or DeliberationPolicy()
        stream = on_event is not None if stream is None else stream
        context = self.session_manager.get_context(session_id)
        fingerprint = hashlib.sha256(json.dumps({
            "agents": {k: v.model_dump() for k, v in agent_configs.items()},
            "config": deliberation_config.model_dump(), "source": user_message,
            "phase_contexts": context.metadata.get("phase_contexts", {}),
            "policy": f"{type(policy).__module__}.{type(policy).__qualname__}",
            "policy_config": policy.cache_key(),
        }, sort_keys=True).encode()).hexdigest()

        async def persist(_: PhaseResult | None = None) -> None:
            current = await self.session_manager.get(session_id)
            if current:
                result.duration_seconds = prior_duration + time.monotonic() - started
                result.total_cost = prior_cost + self.llm_client.session_cost(session_id) - cost_at_start
                current.result = result.model_dump(mode="json")
                await self.session_manager.update(current)

        self._active.add(session_id)
        try:
            session = await self.session_manager.get(session_id)
            if not session:
                raise ValueError(f"Session {session_id} not found")
            saved = session.result or {}
            if resume and saved.get("metadata", {}).get("execution_fingerprint") == fingerprint:
                result = DeliberationResult.model_validate(saved)
                prior_duration, prior_cost = result.duration_seconds, max(result.total_cost, cost_at_start)
                result.final_output = None
            context.clear_deliberation()
            result.metadata.update(execution_fingerprint=fingerprint, status="running")
            result.metadata.pop("error", None)
            completed = result.metadata.setdefault("completed_phases", [])
            session.status, session.error = SessionStatus.RUNNING, None
            await self.session_manager.update(session)
            agents = {aid: self._create_agent(aid, cfg) for aid, cfg in agent_configs.items()}
            restored = {p.phase_name: p for p in result.phases}
            result.phases = []
            independent_ids: set[str] | None = None
            for phase_config in deliberation_config.phases:
                phase = restored.get(phase_config.name) or PhaseResult(
                    phase_name=phase_config.name, mode=phase_config.mode,
                )
                result.phases.append(phase)
                # Hydrate only phases reached so far. Future saved phases never leak
                # into an earlier phase's independent assessments or discussion.
                if phase.mode != "round_robin" or phase.phase_name in completed:
                    for message in phase.messages:
                        context.add_message(message)
                    for output in phase.outputs:
                        context.add_output(output)
                if on_event:
                    on_event("phase_start", {"phase": phase.phase_name, "resumed": phase.phase_name in completed})
                if phase.phase_name not in completed:
                    ordered = [agents[aid] for aid in phase_config.agents]
                    if (deliberation_config.pattern == "independent_synthesis"
                            and phase.mode == "round_robin" and independent_ids is not None):
                        ordered = [a for a in ordered if a.agent_id in independent_ids]
                    if not ordered:
                        raise RuntimeError(f"No successful participants available for '{phase.phase_name}'")
                    rules = DeliberationRules(
                        max_rounds=phase_config.max_rounds,
                        visibility=phase_config.visibility,
                        timeout_seconds=phase_config.timeout_seconds,
                        max_concurrency=deliberation_config.max_concurrency,
                        max_attempts=deliberation_config.max_attempts,
                    )
                    prior_outputs = [o for p in result.phases[:-1] for o in p.outputs]
                    prompt = user_message
                    selector = phase_config.input
                    if selector == "all_prior_outputs" or (
                        selector is None and phase.mode == "sequential" and prior_outputs
                        and deliberation_config.pattern == "independent_synthesis"
                    ):
                        prompt = policy.synthesis_prompt(
                            user_message, prior_outputs,
                            [f for p in result.phases[:-1] for f in p.failed_agents],
                        )
                    elif selector == "previous_output" or (
                        selector is None and deliberation_config.pattern == "sequential" and prior_outputs
                    ):
                        if not prior_outputs:
                            raise ValueError(f"Phase '{phase.phase_name}' has no previous output")
                        prompt = prior_outputs[-1].content
                    kwargs = dict(policy=policy, phase_name=phase.phase_name,
                                  on_progress=persist, before_turn=before_turn, result=phase)
                    if phase.mode == "round_robin":
                        await RoundRobinPattern().execute(
                            ordered, context, rules, on_event, stream, prompt, **kwargs,
                        )
                    else:
                        pattern = ParallelPattern() if phase.mode == "parallel" else SequentialPattern()
                        await pattern.execute(
                            ordered, context, Message(role="user", content=prompt), rules,
                            on_event, stream, **kwargs,
                        )
                    disabled = phase.mode == "round_robin" and phase_config.max_rounds == 0
                    successful = {o.agent_id for o in phase.outputs}
                    if not disabled and len(successful) < phase_config.min_successful_agents:
                        await persist()
                        raise RuntimeError(
                            f"Phase '{phase.phase_name}' produced {len(successful)} valid participant "
                            f"responses; requires {phase_config.min_successful_agents}. "
                            + "; ".join(f"{f['agent_id']}: {f['error']}" for f in phase.failed_agents)
                        )
                    if phase.mode == "sequential" and phase.failed_agents:
                        raise RuntimeError(f"Sequential phase '{phase.phase_name}' failed: {phase.failed_agents[-1]['error']}")
                    completed.append(phase.phase_name)
                    await persist()
                if phase.mode == "parallel" and independent_ids is None:
                    independent_ids = {o.agent_id for o in phase.outputs}
                if on_event:
                    on_event("phase_complete", {
                        "phase": phase.phase_name, "outputs": len(phase.outputs),
                        "failed_agents": phase.failed_agents,
                    })
            result.final_output = next((p.outputs[-1] for p in reversed(result.phases) if p.outputs), None)
            if result.final_output is None:
                raise RuntimeError("Deliberation produced no valid final output")
            result.metadata.update(
                status="completed",
                partial=any(p.failed_agents for p in result.phases),
                configured_deliberation_rounds=sum(p.max_rounds for p in deliberation_config.phases if p.mode == "round_robin"),
                effective_deliberation_rounds=sum(p.max_rounds for p in deliberation_config.phases if p.mode == "round_robin"),
                deliberation_round_policy="configured",
                deliberation_stop_reason="completed_configured_rounds",
            )
            from datetime import datetime, timezone
            result.completed_at = datetime.now(timezone.utc)
            await persist()
            current = await self.session_manager.get(session_id)
            current.status = SessionStatus.COMPLETED if finalize_session else SessionStatus.RUNNING
            await self.session_manager.update(current)
            return result
        except BaseException as exc:
            if session is not None:
                cancelled = isinstance(exc, asyncio.CancelledError)
                result.metadata["status"] = "stopped" if cancelled else "failed"
                result.metadata["error"] = "Deliberation cancelled" if cancelled else sanitize_error_message(exc)
                await persist()
                current = await self.session_manager.get(session_id)
                current.status = SessionStatus.STOPPED if cancelled else SessionStatus.FAILED
                current.error = None if cancelled else result.metadata["error"]
                await self.session_manager.update(current)
            raise
        finally:
            self._active.discard(session_id)
