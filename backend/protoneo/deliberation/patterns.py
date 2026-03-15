"""
Built-in deliberation patterns.

Each pattern orchestrates agents according to a specific interaction
model. Patterns are composable: IndependentSynthesis chains a parallel
phase, an optional round-robin phase, and a sequential synthesis phase.
"""

import asyncio
import logging
import time
from typing import AsyncGenerator, Callable

from ..agents.base import BaseAgent
from ..agents.types import AgentOutput, Message
from .session import SessionContext
from .types import DeliberationResult, DeliberationRules, PhaseResult

logger = logging.getLogger("protoneo.deliberation.patterns")

# Type alias for the event callback that streams phase-level updates
EventCallback = Callable[[str, dict], None] | None


class SequentialPattern:
    """
    Agents run one after another. Each sees all prior outputs.
    Useful for pipeline workflows (parse -> analyze -> synthesize).
    """

    async def execute(
        self,
        agents: list[BaseAgent],
        context: SessionContext,
        user_message: Message,
        rules: DeliberationRules,
        on_event: EventCallback = None,
        stream: bool = False,
    ) -> PhaseResult:
        start = time.monotonic()
        result = PhaseResult(phase_name="sequential", mode="sequential")

        for agent in agents:
            agent_start = time.monotonic()
            if on_event:
                on_event("agent_start", {"agent_id": agent.agent_id, "role": agent.role, "model": agent.model})

            if stream and on_event:
                response = await agent.process_stream(
                    context, user_message,
                    on_token=lambda chunk, aid=agent.agent_id, role=agent.role: on_event(
                        "token", {"agent_id": aid, "role": role, "chunk": chunk}
                    ),
                )
            else:
                response = await agent.process(context, user_message)
            context.add_message(response)
            result.messages.append(response)

            output = AgentOutput(
                agent_id=agent.agent_id,
                agent_role=agent.role,
                content=response.content,
                metadata=response.metadata,
            )
            context.add_output(output)
            result.outputs.append(output)

            if on_event:
                usage = response.metadata.get("usage", {})
                on_event("agent_done", {
                    "agent_id": agent.agent_id,
                    "role": agent.role,
                    "model": agent.model,
                    "duration_seconds": round(time.monotonic() - agent_start, 1),
                    "tokens": usage.get("total_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                })

            # Next agent's user message is the previous agent's output
            user_message = Message(role="user", content=response.content)

        result.duration_seconds = time.monotonic() - start
        return result


class ParallelPattern:
    """
    All agents run concurrently on the same input. They do not see
    each other's outputs (blind). Used for independent review phases.
    """

    async def execute(
        self,
        agents: list[BaseAgent],
        context: SessionContext,
        user_message: Message,
        rules: DeliberationRules,
        on_event: EventCallback = None,
        stream: bool = False,
    ) -> PhaseResult:
        start = time.monotonic()
        result = PhaseResult(phase_name="parallel", mode="parallel")

        failed_agents: list[dict] = []

        async def run_agent(agent: BaseAgent) -> tuple[Message, AgentOutput]:
            agent_start = time.monotonic()
            if on_event:
                on_event("agent_start", {"agent_id": agent.agent_id, "role": agent.role, "model": agent.model})

            if stream and on_event:
                response = await agent.process_stream(
                    context, user_message, include_history=False,
                    on_token=lambda chunk, aid=agent.agent_id, role=agent.role: on_event(
                        "token", {"agent_id": aid, "role": role, "chunk": chunk}
                    ),
                )
            else:
                response = await agent.process(
                    context, user_message, include_history=False
                )

            output = AgentOutput(
                agent_id=agent.agent_id,
                agent_role=agent.role,
                content=response.content,
                metadata=response.metadata,
            )

            if on_event:
                usage = response.metadata.get("usage", {})
                on_event("agent_done", {
                    "agent_id": agent.agent_id,
                    "role": agent.role,
                    "model": agent.model,
                    "duration_seconds": round(time.monotonic() - agent_start, 1),
                    "tokens": usage.get("total_tokens", 0),
                    "completion_tokens": usage.get("completion_tokens", 0),
                })

            return response, output

        tasks = [run_agent(agent) for agent in agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                agent = agents[i]
                logger.error("Agent %s (%s) failed in parallel phase: %s", agent.agent_id, agent.role, r)
                failed_agents.append({"agent_id": agent.agent_id, "role": agent.role, "error": str(r)})
                if on_event:
                    on_event("agent_error", {"agent_id": agent.agent_id, "role": agent.role, "error": str(r)})
                continue
            response, output = r
            context.add_message(response)
            context.add_output(output)
            result.messages.append(response)
            result.outputs.append(output)

        result.duration_seconds = time.monotonic() - start
        if failed_agents:
            logger.warning(
                "Parallel phase completed with %d/%d agents failed: %s",
                len(failed_agents), len(agents),
                [f["agent_id"] for f in failed_agents],
            )
        return result


class RoundRobinPattern:
    """
    Agents take turns responding, each seeing all prior messages.
    Continues until max_rounds is reached.
    """

    async def execute(
        self,
        agents: list[BaseAgent],
        context: SessionContext,
        rules: DeliberationRules,
        on_event: EventCallback = None,
        stream: bool = False,
    ) -> PhaseResult:
        start = time.monotonic()
        result = PhaseResult(phase_name="round_robin", mode="round_robin")

        # Build a summary of all prior outputs for the opening prompt
        prior_outputs = []
        for agent_id, outputs in context.agent_outputs.items():
            for o in outputs:
                prior_outputs.append(f"[{o.agent_role}]: {o.content}")
        prior_text = "\n\n---\n\n".join(prior_outputs)

        for round_num in range(rules.max_rounds):
            if on_event:
                on_event("round_start", {"round": round_num + 1})

            for agent in agents:
                prompt = (
                    f"This is round {round_num + 1} of deliberation.\n\n"
                    f"Prior reviews and discussion:\n{prior_text}\n\n"
                    f"Please respond to the other reviewers' points. "
                    f"Update your assessment if warranted."
                )
                msg = Message(role="user", content=prompt)

                if on_event:
                    on_event("agent_start", {
                        "agent_id": agent.agent_id,
                        "role": agent.role,
                        "model": agent.model,
                        "round": round_num + 1,
                    })

                try:
                    if stream and on_event:
                        response = await agent.process_stream(
                            context, msg,
                            on_token=lambda chunk, aid=agent.agent_id, r=round_num + 1: on_event(
                                "token", {"agent_id": aid, "role": agent.role, "round": r, "chunk": chunk}
                            ),
                        )
                    else:
                        response = await agent.process(context, msg)
                except Exception as exc:
                    logger.error(
                        "Agent %s (%s) failed in round %d: %s",
                        agent.agent_id, agent.role, round_num + 1, exc,
                    )
                    if on_event:
                        on_event("agent_error", {
                            "agent_id": agent.agent_id,
                            "role": agent.role,
                            "round": round_num + 1,
                            "error": str(exc),
                        })
                    continue

                context.add_message(response)
                result.messages.append(response)

                output = AgentOutput(
                    agent_id=agent.agent_id,
                    agent_role=agent.role,
                    content=response.content,
                    metadata={**response.metadata, "round": round_num + 1},
                )
                context.add_output(output)
                result.outputs.append(output)

                prior_text += f"\n\n---\n\n[{agent.role} (round {round_num + 1})]: {response.content}"

                if on_event:
                    on_event("agent_done", {
                        "agent_id": agent.agent_id,
                        "role": agent.role,
                        "model": agent.model,
                        "round": round_num + 1,
                    })

        result.duration_seconds = time.monotonic() - start
        return result


class IndependentSynthesisPattern:
    """
    The primary pattern for the PC Paper Reviewer product.

    Phase 1: All reviewer agents work in parallel (blind).
    Phase 2: Round-robin deliberation where reviewers see each other.
    Phase 3: A synthesizer agent produces the final output.
    """

    def __init__(self):
        self._parallel = ParallelPattern()
        self._round_robin = RoundRobinPattern()
        self._sequential = SequentialPattern()

    async def execute(
        self,
        reviewers: list[BaseAgent],
        synthesizer: BaseAgent,
        context: SessionContext,
        user_message: Message,
        rules: DeliberationRules,
        on_event: EventCallback = None,
        stream: bool = False,
    ) -> DeliberationResult:
        start = time.monotonic()
        phases: list[PhaseResult] = []

        # Phase 1: Independent parallel review
        if on_event:
            on_event("phase_start", {"phase": "independent_review"})
        phase1 = await self._parallel.execute(
            reviewers, context, user_message, rules, on_event, stream=stream
        )
        phase1.phase_name = "independent_review"
        phases.append(phase1)

        if not phase1.outputs:
            logger.error("All reviewers failed in Phase 1. Cannot proceed to deliberation.")
            if on_event:
                on_event("error", {"message": "All reviewers failed. No reviews to deliberate."})
            return DeliberationResult(
                session_id=context.session_id,
                phases=phases,
                final_output=None,
                duration_seconds=time.monotonic() - start,
                metadata={"aborted": True, "reason": "all_reviewers_failed"},
            )

        if len(phase1.outputs) < len(reviewers):
            failed_count = len(reviewers) - len(phase1.outputs)
            logger.warning(
                "Phase 1 completed with %d/%d reviewers. Continuing with partial results.",
                len(phase1.outputs), len(reviewers),
            )
            if on_event:
                on_event("phase_warning", {
                    "phase": "independent_review",
                    "message": f"{failed_count} reviewer(s) failed. Continuing with {len(phase1.outputs)} reviews.",
                })

        # Phase 2: Deliberation (round-robin)
        if rules.max_rounds > 0:
            if on_event:
                on_event("phase_start", {"phase": "deliberation"})
            phase2 = await self._round_robin.execute(
                reviewers, context, rules, on_event, stream=stream
            )
            phase2.phase_name = "deliberation"
            phases.append(phase2)

        # Phase 3: Meta-review (synthesis)
        if on_event:
            on_event("phase_start", {"phase": "meta_review"})

        # Build synthesis prompt from all prior outputs
        all_outputs = []
        for phase in phases:
            for o in phase.outputs:
                all_outputs.append(f"[{o.agent_role}]: {o.content}")
        synthesis_prompt = Message(
            role="user",
            content=(
                "Below are all reviewer outputs and deliberation messages. "
                "Synthesize them into a final meta-review.\n\n"
                + "\n\n---\n\n".join(all_outputs)
            ),
        )

        phase3 = await self._sequential.execute(
            [synthesizer], context, synthesis_prompt, rules, on_event, stream=stream
        )
        phase3.phase_name = "meta_review"
        phases.append(phase3)

        final_output = phase3.outputs[0] if phase3.outputs else None
        session_id = context.session_id

        return DeliberationResult(
            session_id=session_id,
            phases=phases,
            final_output=final_output,
            duration_seconds=time.monotonic() - start,
        )
