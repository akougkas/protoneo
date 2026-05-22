"""
Deliberation engine.

Orchestrates agents through deliberation patterns. The engine is the
main entry point for running a deliberation session.
"""

import asyncio
import logging
import time
from typing import Any, Callable

from ..agents.base import BaseAgent
from ..agents.types import Message
from ..config.schema import AgentConfig, DeliberationConfig, PhaseConfig
from ..llm.client import LLMClient
from .patterns import (
    IndependentSynthesisPattern,
    ParallelPattern,
    RoundRobinPattern,
    SequentialPattern,
)
from .session import Session, SessionContext, SessionManager, SessionStatus
from .types import DeliberationResult, DeliberationRules

logger = logging.getLogger("protoneo.deliberation.engine")

EventCallback = Callable[[str, dict], None] | None


class DeliberationEngine:
    """
    Runs deliberation sessions.

    The engine resolves agent configs into BaseAgent instances,
    selects the appropriate pattern, and executes the deliberation.
    """

    def __init__(self, llm_client: LLMClient, session_manager: SessionManager):
        self.llm_client = llm_client
        self.session_manager = session_manager

    def _create_agent(self, agent_id: str, config: AgentConfig) -> BaseAgent:
        return BaseAgent(
            agent_id=agent_id,
            role=config.role,
            model=config.model,
            system_prompt=config.system_prompt,
            llm_client=self.llm_client,
            focus=config.focus,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            top_p=config.top_p,
            top_k=config.top_k,
            min_p=config.min_p,
            repeat_penalty=config.repeat_penalty,
            reasoning_effort=config.reasoning_effort,
            presence_penalty=config.presence_penalty,
            frequency_penalty=config.frequency_penalty,
        )

    async def run(
        self,
        session_id: str,
        agent_configs: dict[str, AgentConfig],
        deliberation_config: DeliberationConfig,
        user_message: str,
        on_event: EventCallback = None,
    ) -> DeliberationResult:
        """
        Execute a full deliberation session.

        Args:
            session_id: The session to run in.
            agent_configs: Map of agent_id -> AgentConfig.
            deliberation_config: The deliberation pattern and phase definitions.
            user_message: The initial prompt or document content.
            on_event: Optional callback for streaming phase-level events.

        Returns:
            The complete DeliberationResult.
        """
        session = await self.session_manager.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        session.status = SessionStatus.RUNNING
        await self.session_manager.update(session)

        context = self.session_manager.get_context(session_id)
        agents = {aid: self._create_agent(aid, cfg) for aid, cfg in agent_configs.items()}
        msg = Message(role="user", content=user_message)

        try:
            result = await self._execute_pattern(
                deliberation_config, agents, context, msg, on_event
            )
            result.total_cost = self.llm_client.session_cost(session_id)

            session.status = SessionStatus.COMPLETED
            session.result = result.model_dump(mode="json")
            await self.session_manager.update(session)

            logger.info(
                "Session %s completed in %.1fs (cost=$%.4f)",
                session_id,
                result.duration_seconds,
                result.total_cost,
            )
            return result

        except Exception as e:
            session.status = SessionStatus.FAILED
            session.error = str(e)
            await self.session_manager.update(session)
            logger.error("Session %s failed: %s", session_id, e)
            raise

    async def _execute_pattern(
        self,
        config: DeliberationConfig,
        agents: dict[str, BaseAgent],
        context: SessionContext,
        user_message: Message,
        on_event: EventCallback,
    ) -> DeliberationResult:
        """Dispatch to the correct pattern based on config."""
        pattern_name = config.pattern

        if pattern_name == "independent_synthesis":
            return await self._run_independent_synthesis(
                config, agents, context, user_message, on_event
            )

        if pattern_name == "sequential":
            return await self._run_sequential(
                config, agents, context, user_message, on_event
            )

        if pattern_name == "round_robin":
            return await self._run_round_robin(
                config, agents, context, user_message, on_event
            )

        raise ValueError(f"Unknown deliberation pattern: {pattern_name}")

    async def _run_independent_synthesis(
        self,
        config: DeliberationConfig,
        agents: dict[str, BaseAgent],
        context: SessionContext,
        user_message: Message,
        on_event: EventCallback,
    ) -> DeliberationResult:
        """Run the Independent+Synthesis pattern from phase config."""
        reviewer_ids: list[str] = []
        synthesizer_id: str | None = None
        max_rounds = 0

        for phase in config.phases:
            if phase.mode == "parallel":
                reviewer_ids = phase.agents
            elif phase.mode == "round_robin":
                max_rounds = phase.max_rounds
                # Round-robin uses the same agents as parallel by default
                if not reviewer_ids:
                    reviewer_ids = phase.agents
            elif phase.mode == "sequential":
                if phase.agents:
                    synthesizer_id = phase.agents[0]

        reviewers = [agents[aid] for aid in reviewer_ids if aid in agents]
        if not reviewers:
            raise ValueError("No reviewer agents configured for independent_synthesis")

        synthesizer = agents.get(synthesizer_id) if synthesizer_id else None
        if not synthesizer:
            raise ValueError("No synthesizer agent configured for independent_synthesis")

        rules = DeliberationRules(max_rounds=max_rounds)
        pattern = IndependentSynthesisPattern()
        stream = on_event is not None
        return await pattern.execute(reviewers, synthesizer, context, user_message, rules, on_event, stream=stream)

    async def _run_sequential(
        self,
        config: DeliberationConfig,
        agents: dict[str, BaseAgent],
        context: SessionContext,
        user_message: Message,
        on_event: EventCallback,
    ) -> DeliberationResult:
        start = time.monotonic()
        all_agent_ids = []
        for phase in config.phases:
            all_agent_ids.extend(phase.agents)

        ordered = [agents[aid] for aid in all_agent_ids if aid in agents]
        rules = DeliberationRules()
        pattern = SequentialPattern()
        stream = on_event is not None
        phase_result = await pattern.execute(ordered, context, user_message, rules, on_event, stream=stream)

        return DeliberationResult(
            session_id=context.session_id,
            phases=[phase_result],
            final_output=phase_result.outputs[-1] if phase_result.outputs else None,
            duration_seconds=time.monotonic() - start,
        )

    async def _run_round_robin(
        self,
        config: DeliberationConfig,
        agents: dict[str, BaseAgent],
        context: SessionContext,
        user_message: Message,
        on_event: EventCallback,
    ) -> DeliberationResult:
        start = time.monotonic()
        max_rounds = 3
        agent_ids: list[str] = []

        for phase in config.phases:
            agent_ids.extend(phase.agents)
            if phase.max_rounds:
                max_rounds = phase.max_rounds

        ordered = [agents[aid] for aid in agent_ids if aid in agents]
        rules = DeliberationRules(max_rounds=max_rounds)

        # Seed context with the initial message
        context.add_message(user_message)

        pattern = RoundRobinPattern()
        stream = on_event is not None
        phase_result = await pattern.execute(
            ordered, context, rules, on_event, stream=stream,
            paper_context=user_message.content if user_message else "",
        )

        return DeliberationResult(
            session_id=context.session_id,
            phases=[phase_result],
            final_output=phase_result.outputs[-1] if phase_result.outputs else None,
            duration_seconds=time.monotonic() - start,
        )
