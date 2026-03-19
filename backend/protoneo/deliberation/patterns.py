"""
Built-in deliberation patterns.

Each pattern orchestrates agents according to a specific interaction
model. Patterns are composable: IndependentSynthesis chains a parallel
phase, an optional round-robin phase, and a sequential synthesis phase.
"""

import asyncio
import logging
import time
from typing import Callable

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

        # Collect agents that need retry (exceptions or empty output)
        retry_agents: list[BaseAgent] = []

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                agent = agents[i]
                logger.warning("Agent %s (%s) failed in parallel phase: %s (will retry)", agent.agent_id, agent.role, r)
                retry_agents.append(agent)
                if on_event:
                    on_event("agent_warning", {"agent_id": agent.agent_id, "role": agent.role, "message": f"Failed: {r}. Retrying..."})
                continue
            response, output = r
            # Fix 1: Check for empty content after thinking-strip
            if not output.content or not output.content.strip():
                agent = agents[i]
                logger.warning(
                    "Agent %s (%s) returned empty output, scheduling retry",
                    agent.agent_id, agent.role,
                )
                retry_agents.append(agent)
                if on_event:
                    on_event("agent_warning", {
                        "agent_id": agent.agent_id, "role": agent.role,
                        "message": "Empty output, retrying...",
                    })
                continue
            context.add_message(response)
            context.add_output(output)
            result.messages.append(response)
            result.outputs.append(output)

        # Retry failed/empty agents once (sequentially to avoid contention)
        for agent in retry_agents:
            logger.info("Retrying agent %s (%s)", agent.agent_id, agent.role)
            if on_event:
                on_event("agent_retry", {"agent_id": agent.agent_id, "role": agent.role})
            try:
                response, output = await run_agent(agent)
                if output.content and output.content.strip():
                    context.add_message(response)
                    context.add_output(output)
                    result.messages.append(response)
                    result.outputs.append(output)
                    logger.info("Retry succeeded for agent %s", agent.agent_id)
                else:
                    logger.error("Agent %s still empty after retry", agent.agent_id)
                    failed_agents.append({
                        "agent_id": agent.agent_id, "role": agent.role,
                        "error": "Empty output after retry",
                    })
                    if on_event:
                        on_event("agent_error", {
                            "agent_id": agent.agent_id, "role": agent.role,
                            "error": "Empty output after retry",
                        })
            except Exception as retry_exc:
                logger.error("Retry failed for agent %s: %s", agent.agent_id, retry_exc)
                failed_agents.append({
                    "agent_id": agent.agent_id, "role": agent.role,
                    "error": str(retry_exc),
                })
                if on_event:
                    on_event("agent_error", {
                        "agent_id": agent.agent_id, "role": agent.role,
                        "error": f"Retry failed: {retry_exc}",
                    })

        result.failed_agents = failed_agents
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

        # Label prior outputs with naturalistic peer transcript framing.
        def _label_prior_outputs(current_agent_id: str) -> str:
            parts = []
            for agent_id, outputs in context.agent_outputs.items():
                for o in outputs:
                    if agent_id == current_agent_id:
                        header = f"--- [YOUR PRIOR ASSESSMENT as {o.agent_role}] ---"
                    else:
                        header = f"--- [BEGIN PEER TRANSCRIPT: Reviewer ({o.agent_role})] ---"
                    footer = "--- [END PEER TRANSCRIPT] ---" if agent_id != current_agent_id else ""
                    block = f"{header}\n{o.content}"
                    if footer:
                        block += f"\n{footer}"
                    parts.append(block)
            return "\n\n".join(parts)

        # Fix 12: Track previous round output for duplicate detection
        prev_round_outputs: dict[str, str] = {}

        for round_num in range(rules.max_rounds):
            if on_event:
                on_event("round_start", {"round": round_num + 1})

            for agent in agents:
                labeled_prior = _label_prior_outputs(agent.agent_id)
                prompt = (
                    f"This is round {round_num + 1} of deliberation.\n\n"
                    f"You are reading the reviews of your peers. Address them "
                    f"naturally by their role in your response (e.g., 'I agree "
                    f"with the Systems reviewer, but...').\n\n"
                    f"Prior reviews and discussion:\n{labeled_prior}\n\n"
                    f"You are the {agent.role}. Respond from your assigned "
                    f"perspective and expertise. Your analysis should reflect "
                    f"your unique vantage point.\n\n"
                    f"Engage with the other reviewers' arguments. Concede points "
                    f"where the evidence is convincing. Push back where you "
                    f"disagree, citing specific manuscript evidence."
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
                    logger.warning(
                        "Agent %s (%s) failed in round %d: %s (retrying once)",
                        agent.agent_id, agent.role, round_num + 1, exc,
                    )
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
                    except Exception as retry_exc:
                        logger.error(
                            "Agent %s (%s) retry failed in round %d: %s",
                            agent.agent_id, agent.role, round_num + 1, retry_exc,
                        )
                        if on_event:
                            on_event("agent_error", {
                                "agent_id": agent.agent_id,
                                "role": agent.role,
                                "round": round_num + 1,
                                "error": str(retry_exc),
                            })
                        result.failed_agents.append({
                            "agent_id": agent.agent_id,
                            "role": agent.role,
                            "round": round_num + 1,
                            "error": str(retry_exc),
                        })
                        continue

                context.add_message(response)
                result.messages.append(response)

                # Fix 12: Detect near-duplicate outputs across agents in same round
                content_trimmed = response.content.strip()
                for other_aid, other_text in prev_round_outputs.items():
                    if other_aid != agent.agent_id and other_text:
                        # Simple character-level similarity check
                        shorter = min(len(content_trimmed), len(other_text))
                        if shorter > 100:
                            common = sum(a == b for a, b in zip(content_trimmed, other_text))
                            similarity = common / shorter
                            if similarity > 0.90:
                                logger.warning(
                                    "Near-duplicate deliberation output: %s and %s "
                                    "are %.0f%% similar in round %d",
                                    agent.agent_id, other_aid, similarity * 100, round_num + 1,
                                )
                                if on_event:
                                    on_event("duplicate_warning", {
                                        "agents": [agent.agent_id, other_aid],
                                        "similarity": round(similarity, 3),
                                        "round": round_num + 1,
                                    })
                prev_round_outputs[agent.agent_id] = content_trimmed

                output = AgentOutput(
                    agent_id=agent.agent_id,
                    agent_role=agent.role,
                    content=response.content,
                    metadata={**response.metadata, "round": round_num + 1},
                )
                context.add_output(output)
                result.outputs.append(output)

                if on_event:
                    on_event("agent_done", {
                        "agent_id": agent.agent_id,
                        "role": agent.role,
                        "model": agent.model,
                        "round": round_num + 1,
                    })

        result.duration_seconds = time.monotonic() - start
        return result


def _extract_merit_scores(phase: PhaseResult) -> list[float]:
    """Parse numerical overall_merit scores from phase outputs.

    Searches each output for JSON with an overall_merit.score field.
    Returns a list of extracted scores (empty if parsing fails).
    """
    import json as _json
    import re as _re

    scores: list[float] = []
    for output in phase.outputs:
        text = output.content or ""
        # Try to find overall_merit in JSON output
        try:
            # Strip markdown fences
            cleaned = _re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
            cleaned = _re.sub(r"\n?\s*```\s*$", "", cleaned).strip()
            parsed = _json.loads(cleaned)
            if isinstance(parsed, dict):
                merit = parsed.get("overall_merit", {})
                if isinstance(merit, dict) and "score" in merit:
                    scores.append(float(merit["score"]))
                elif isinstance(merit, (int, float)):
                    scores.append(float(merit))
                continue
        except (ValueError, _json.JSONDecodeError):
            pass
        # Fallback: search for the pattern in raw text
        match = _re.search(r'"overall_merit"\s*:\s*\{[^}]*"score"\s*:\s*(\d+)', text)
        if match:
            scores.append(float(match.group(1)))
    return scores


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

        # Phase 2: Deliberation (round-robin) with variance-triggered adjustment
        # Only include reviewers that produced output in Phase 1.
        if rules.max_rounds > 0:
            failed_ids = {f["agent_id"] for f in phase1.failed_agents}
            succeeded_ids = {o.agent_id for o in phase1.outputs}
            deliberation_reviewers = [
                r for r in reviewers
                if r.agent_id not in failed_ids and r.agent_id in succeeded_ids
            ]
            if not deliberation_reviewers:
                logger.error("No reviewers available for deliberation after Phase 1 failures.")
            else:
                if len(deliberation_reviewers) < len(reviewers):
                    excluded = [r.agent_id for r in reviewers if r.agent_id not in succeeded_ids]
                    logger.warning(
                        "Excluding %d agent(s) from deliberation (no Phase 1 output): %s",
                        len(excluded), excluded,
                    )
                    if on_event:
                        on_event("phase_warning", {
                            "phase": "deliberation",
                            "message": f"Excluded agents with no Phase 1 output: {excluded}",
                        })

                # Variance-triggered round adjustment: parse merit scores from
                # Phase 1 outputs and adapt deliberation depth accordingly.
                effective_rounds = rules.max_rounds
                merit_scores = _extract_merit_scores(phase1)
                if len(merit_scores) >= 2:
                    score_spread = max(merit_scores) - min(merit_scores)
                    if score_spread <= 1.0:
                        effective_rounds = min(1, rules.max_rounds)
                        logger.info(
                            "[Kernel] Consensus achieved (spread=%.1f, scores=%s). "
                            "Running 1 synthesis round.",
                            score_spread, merit_scores,
                        )
                        if on_event:
                            on_event("consensus_detected", {
                                "spread": score_spread,
                                "scores": merit_scores,
                                "effective_rounds": effective_rounds,
                            })
                    elif score_spread >= 2.0:
                        effective_rounds = min(max(rules.max_rounds, 3), 4)
                        logger.info(
                            "[Kernel] Contested reviews (spread=%.1f, scores=%s). "
                            "Deep deliberation: %d rounds.",
                            score_spread, merit_scores, effective_rounds,
                        )
                        if on_event:
                            on_event("contested_detected", {
                                "spread": score_spread,
                                "scores": merit_scores,
                                "effective_rounds": effective_rounds,
                            })

                adjusted_rules = DeliberationRules(
                    max_rounds=effective_rounds,
                    timeout_seconds=rules.timeout_seconds,
                    visibility=rules.visibility,
                )

                if on_event:
                    on_event("phase_start", {"phase": "deliberation"})
                phase2 = await self._round_robin.execute(
                    deliberation_reviewers, context, adjusted_rules, on_event, stream=stream
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

        # Include the original paper context (graph summary, paper text) so
        # the meta-reviewer can ground its synthesis in the source material,
        # not just reviewer opinions.
        original_context = user_message.content if user_message else ""
        context_block = ""
        if original_context:
            # Extract the graph summary portion (after the manuscript)
            graph_marker = "## Paper Knowledge Graph"
            if graph_marker in original_context:
                graph_idx = original_context.index(graph_marker)
                context_block = (
                    "\n\nThe following knowledge graph summary was provided to reviewers:\n\n"
                    + original_context[graph_idx:]
                )

        synthesis_prompt = Message(
            role="user",
            content=(
                "Below are all reviewer outputs and deliberation messages. "
                "Synthesize them into a final meta-review.\n\n"
                + "\n\n---\n\n".join(all_outputs)
                + context_block
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
