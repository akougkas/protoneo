"""Composable execution patterns with bounded calls and validated output commits."""

import asyncio
import time
from collections.abc import Awaitable, Callable

from ..agents.base import BaseAgent
from ..agents.types import AgentOutput, Message
from ..llm.errors import sanitize_error_message
from .policy import DeliberationPolicy
from .session import SessionContext
from .types import DeliberationResult, DeliberationRules, PhaseResult

EventCallback = Callable[[str, dict], None] | None
ProgressCallback = Callable[[PhaseResult], Awaitable[None]] | None
BeforeTurn = Callable[[], Awaitable[None]] | None


async def _run_agent(
    agent: BaseAgent, context: SessionContext, message: Message,
    rules: DeliberationRules, result: PhaseResult, policy: DeliberationPolicy,
    on_event: EventCallback, stream: bool, round_number: int = 0,
    before_turn: BeforeTurn = None,
) -> tuple[Message, AgentOutput] | dict:
    """Retry invalid or failed answers; never publish an unvalidated response."""
    started = time.monotonic()
    identity = {"agent_id": agent.agent_id, "role": agent.role, "model": agent.model}
    if round_number:
        identity["round"] = round_number
    prompt = message.content
    extra = policy.instructions(result.phase_name, result.mode)
    if extra and result.mode != "round_robin":
        prompt += "\n\n" + extra
    error = ""
    for attempt in range(1, rules.max_attempts + 1):
        if before_turn:
            await before_turn()
        if on_event:
            on_event("agent_start", {**identity, "attempt": attempt})
            on_event("prompt_rendered", {**identity, "phase": result.phase_name, "text": prompt})
        try:
            async with asyncio.timeout(rules.timeout_seconds):
                msg = Message(role="user", content=prompt)
                if stream and on_event:
                    response = await agent.process_stream(
                        context, msg, include_history=False,
                        on_token=lambda chunk: on_event("token", {**identity, "chunk": chunk}),
                    )
                else:
                    response = await agent.process(context, msg, include_history=False)
                structured = policy.validate_output(
                    response.content, phase_name=result.phase_name, mode=result.mode,
                    agent=agent, context=context,
                )
            metadata = {**response.metadata, "phase": result.phase_name, "attempts": attempt}
            if round_number:
                metadata.update(round=round_number, round_id=f"round-{round_number}",
                                speaker_id=agent.agent_id, speaker_role=agent.role)
            response = response.model_copy(update={"metadata": metadata})
            output = AgentOutput(
                agent_id=agent.agent_id, agent_role=agent.role,
                content=response.content, structured=structured, metadata=metadata,
            )
            output.metadata["duration_seconds"] = round(time.monotonic() - started, 3)
            return response, output
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = (f"No valid response within {rules.timeout_seconds:g}s"
                     if isinstance(exc, TimeoutError) else sanitize_error_message(exc))
            if attempt < rules.max_attempts:
                if on_event:
                    on_event("agent_retry", {**identity, "attempt": attempt + 1, "message": error})
                # Keep the same source and peer context. Do not seed an invalid answer
                # as evidence, or fabricate a structured object to conceal the failure.
                prompt = message.content + ("\n\n" + extra if extra else "") + (
                    f"\n\nYour previous response could not be accepted: {error}. "
                    "Return a complete answer satisfying the output contract."
                )
    failure = {**identity, "error": error}
    if on_event:
        on_event("agent_error", failure)
    return failure


async def _commit(
    response: Message, output: AgentOutput, context: SessionContext,
    result: PhaseResult, on_event: EventCallback, on_progress: ProgressCallback,
) -> None:
    if output.metadata.get("round"):
        output.metadata["deliberation_turn"] = len(result.outputs) + 1
    context.add_message(response)
    context.add_output(output)
    result.messages.append(response)
    result.outputs.append(output)
    if on_progress:
        await on_progress(result)
    if on_event:
        usage = output.metadata.get("usage", {})
        on_event("agent_done", {
            "agent_id": output.agent_id, "role": output.agent_role,
            "model": output.metadata.get("model", ""),
            "round": output.metadata.get("round", 0),
            "content": output.content, "structured": output.structured,
            "duration_seconds": output.metadata.get("duration_seconds", 0),
            "tokens": usage.get("total_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
        })


class ParallelPattern:
    async def execute(
        self, agents: list[BaseAgent], context: SessionContext,
        user_message: Message, rules: DeliberationRules,
        on_event: EventCallback = None, stream: bool = False, *,
        policy: DeliberationPolicy | None = None, phase_name: str = "parallel",
        on_progress: ProgressCallback = None, before_turn: BeforeTurn = None,
        result: PhaseResult | None = None,
    ) -> PhaseResult:
        started = time.monotonic()
        result = result or PhaseResult(phase_name=phase_name, mode="parallel")
        policy = policy or DeliberationPolicy()
        done = {o.agent_id for o in result.outputs}
        result.failed_agents = []
        semaphore = asyncio.Semaphore(rules.max_concurrency)
        # Each participant receives the same pre-phase state, including on retry.
        snapshot = context.snapshot()

        async def run(agent):
            async with semaphore:
                return await _run_agent(agent, snapshot, user_message, rules, result,
                                        policy, on_event, stream, before_turn=before_turn)

        tasks = [asyncio.create_task(run(agent)) for agent in agents if agent.agent_id not in done]
        try:
            for task in asyncio.as_completed(tasks):
                item = await task
                if isinstance(item, dict):
                    result.failed_agents.append(item)
                else:
                    await _commit(*item, context, result, on_event, on_progress)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # Completion order should not bias the next phase's participant order.
            order = {agent.agent_id: i for i, agent in enumerate(agents)}
            result.outputs.sort(key=lambda o: order[o.agent_id])
            result.messages.sort(key=lambda m: order[m.agent_id])
            result.duration_seconds += time.monotonic() - started
        return result


class SequentialPattern:
    async def execute(
        self, agents: list[BaseAgent], context: SessionContext,
        user_message: Message, rules: DeliberationRules,
        on_event: EventCallback = None, stream: bool = False, *,
        policy: DeliberationPolicy | None = None, phase_name: str = "sequential",
        on_progress: ProgressCallback = None, before_turn: BeforeTurn = None,
        result: PhaseResult | None = None,
    ) -> PhaseResult:
        started = time.monotonic()
        result = result or PhaseResult(phase_name=phase_name, mode="sequential")
        policy = policy or DeliberationPolicy()
        done = {o.agent_id: o for o in result.outputs}
        result.failed_agents = []
        try:
            for agent in agents:
                if agent.agent_id in done:
                    output = done[agent.agent_id]
                else:
                    item = await _run_agent(agent, context, user_message, rules, result,
                                            policy, on_event, stream, before_turn=before_turn)
                    if isinstance(item, dict):
                        result.failed_agents.append(item)
                        break  # Downstream agents cannot consume a missing pipeline result.
                    response, output = item
                    await _commit(response, output, context, result, on_event, on_progress)
                if rules.visibility == "open":
                    user_message = Message(role="user", content=output.content)
        finally:
            result.duration_seconds += time.monotonic() - started
        return result


class RoundRobinPattern:
    async def execute(
        self, agents: list[BaseAgent], context: SessionContext, rules: DeliberationRules,
        on_event: EventCallback = None, stream: bool = False, paper_context: str = "", *,
        policy: DeliberationPolicy | None = None, phase_name: str = "round_robin",
        on_progress: ProgressCallback = None, before_turn: BeforeTurn = None,
        result: PhaseResult | None = None,
    ) -> PhaseResult:
        started = time.monotonic()
        result = result or PhaseResult(phase_name=phase_name, mode="round_robin")
        policy = policy or DeliberationPolicy()
        saved = {(o.agent_id, o.metadata.get("round")): o for o in result.outputs}
        # A missing earlier turn changes every later speaker's evidence. Reuse
        # only the accepted prefix, hydrating it as the conversation advances.
        prefix = []
        for number in range(1, rules.max_rounds + 1):
            offset = (number - 1) % len(agents)
            for agent in agents[offset:] + agents[:offset]:
                output = saved.get((agent.agent_id, number))
                if output is None:
                    break
                prefix.append(output)
            else:
                continue
            break
        result.outputs = prefix
        done = {(o.agent_id, o.metadata.get("round")): o for o in prefix}
        result.messages = [m for m in result.messages
                           if (m.agent_id, m.metadata.get("round")) in done]
        result.failed_agents = []
        try:
            for round_number in range(1, rules.max_rounds + 1):
                if on_event:
                    on_event("round_start", {"round": round_number})
                # Rotate who speaks first so one participant does not always anchor a round.
                offset = (round_number - 1) % len(agents)
                for agent in agents[offset:] + agents[:offset]:
                    if (agent.agent_id, round_number) in done:
                        context.add_output(done[(agent.agent_id, round_number)])
                        for message in result.messages:
                            if message.agent_id == agent.agent_id and message.metadata.get("round") == round_number:
                                context.add_message(message)
                        continue
                    prompt = policy.discussion_prompt(
                        agent=agent, context=context, source=paper_context,
                        phase_name=phase_name, round_number=round_number,
                        blind=rules.visibility == "blind",
                    )
                    item = await _run_agent(
                        agent, context, Message(role="user", content=prompt), rules,
                        result, policy, on_event, stream, round_number, before_turn,
                    )
                    if isinstance(item, dict):
                        result.failed_agents.append(item)
                    else:
                        await _commit(*item, context, result, on_event, on_progress)
                if on_event:
                    on_event("round_complete", {"round": round_number})
        finally:
            result.duration_seconds += time.monotonic() - started
        return result


class IndependentSynthesisPattern:
    """Convenience composition; durable execution is handled by DeliberationEngine."""

    async def execute(
        self, reviewers: list[BaseAgent], synthesizer: BaseAgent,
        context: SessionContext, user_message: Message, rules: DeliberationRules,
        on_event: EventCallback = None, stream: bool = False,
    ) -> DeliberationResult:
        started = time.monotonic()
        phases = []
        policy = DeliberationPolicy()
        if on_event:
            on_event("phase_start", {"phase": "independent_review"})
        phase = await ParallelPattern().execute(
            reviewers, context, user_message, rules, on_event, stream,
            phase_name="independent_review",
        )
        phases.append(phase)
        if not phase.outputs:
            raise RuntimeError("All participants failed to produce an independent assessment")
        successful = {o.agent_id for o in phase.outputs}
        if rules.max_rounds:
            if on_event:
                on_event("phase_start", {"phase": "deliberation"})
            phases.append(await RoundRobinPattern().execute(
                [a for a in reviewers if a.agent_id in successful], context, rules,
                on_event, stream, user_message.content, phase_name="deliberation",
            ))
        prompt = policy.synthesis_prompt(
            user_message.content, [o for p in phases for o in p.outputs],
            [f for p in phases for f in p.failed_agents],
        )
        if on_event:
            on_event("phase_start", {"phase": "meta_review"})
        phase = await SequentialPattern().execute(
            [synthesizer], context, Message(role="user", content=prompt), rules,
            on_event, stream, phase_name="meta_review",
        )
        phases.append(phase)
        if not phase.outputs:
            raise RuntimeError("Synthesis failed to produce a final answer")
        return DeliberationResult(
            session_id=context.session_id, phases=phases, final_output=phase.outputs[-1],
            duration_seconds=time.monotonic() - started,
        )
