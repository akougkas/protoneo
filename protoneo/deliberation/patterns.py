"""
Built-in deliberation patterns.

Each pattern orchestrates agents according to a specific interaction
model. Patterns are composable: IndependentSynthesis chains a parallel
phase, an optional round-robin phase, and a sequential synthesis phase.
"""

import asyncio
import json
import logging
import re
import time
from typing import Callable

from ..agents.base import BaseAgent
from ..agents.types import AgentOutput, Message
from ..llm.errors import sanitize_error_message
from .session import SessionContext
from .types import DeliberationResult, DeliberationRules, PhaseResult

logger = logging.getLogger("protoneo.deliberation.patterns")


def _try_extract_json(text: str) -> dict | None:
    """Best-effort JSON extraction from LLM output for the structured field."""
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    cleaned = re.sub(r"\n?\s*```\s*$", "", cleaned).strip()
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            obj = json.loads(match.group())
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError):
            pass
    return None

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
                structured=_try_extract_json(response.content),
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
                structured=_try_extract_json(response.content),
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
                error = sanitize_error_message(r)
                logger.warning("Agent %s (%s) failed in parallel phase: %s (will retry)", agent.agent_id, agent.role, error)
                retry_agents.append(agent)
                if on_event:
                    on_event("agent_warning", {"agent_id": agent.agent_id, "role": agent.role, "message": f"Failed: {error}. Retrying..."})
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
                error = sanitize_error_message(retry_exc)
                logger.error("Retry failed for agent %s: %s", agent.agent_id, error)
                failed_agents.append({
                    "agent_id": agent.agent_id, "role": agent.role,
                    "error": error,
                })
                if on_event:
                    on_event("agent_error", {
                        "agent_id": agent.agent_id, "role": agent.role,
                        "error": f"Retry failed: {error}",
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
    Agents take turns responding in deliberation rounds.

    Each agent receives a self-contained prompt with the paper context,
    all peer reviews (labeled by role), and prior deliberation turns.
    History is NOT inherited from the session context to avoid
    duplicate, unlabeled review content that biases toward agreement.
    """

    async def execute(
        self,
        agents: list[BaseAgent],
        context: SessionContext,
        rules: DeliberationRules,
        on_event: EventCallback = None,
        stream: bool = False,
        paper_context: str = "",
    ) -> PhaseResult:
        start = time.monotonic()
        result = PhaseResult(phase_name="round_robin", mode="round_robin")

        # Separate Phase 1 independent reviews from deliberation outputs.
        # Independent reviews are the first output per agent_id.
        independent_reviews: dict[str, AgentOutput] = {}
        for agent_id, outputs in context.agent_outputs.items():
            if outputs:
                independent_reviews[agent_id] = outputs[0]

        # Track deliberation turns separately (accumulated across rounds).
        deliberation_turns: list[AgentOutput] = []

        def _build_deliberation_prompt(
            current_agent_id: str,
            current_role: str,
            round_num: int,
        ) -> str:
            """Build a self-contained deliberation prompt.

            Contains: paper context (truncated), all independent reviews
            labeled by role, prior deliberation turns, and instructions
            to engage with specific peer arguments.
            """
            sections = []

            # 1. Paper context (graph summary only to save tokens;
            #    the full paper is already in the agent's system prompt
            #    context from Phase 1).
            if paper_context:
                graph_marker = "## Paper Knowledge Graph"
                if graph_marker in paper_context:
                    graph_idx = paper_context.index(graph_marker)
                    sections.append(
                        "## Manuscript Context\n\n"
                        "The full paper was provided in your initial review. "
                        "Below is the knowledge graph summary for reference "
                        "when citing specific evidence.\n\n"
                        + paper_context[graph_idx:]
                    )

            # 2. All independent reviews, clearly labeled
            sections.append("## Independent Reviews from the Panel")
            for agent_id, review in independent_reviews.items():
                if agent_id == current_agent_id:
                    label = f"YOUR INDEPENDENT REVIEW ({review.agent_role})"
                else:
                    label = f"PEER REVIEW: {review.agent_role}"
                sections.append(
                    f"--- [{label}] ---\n"
                    f"{review.content}\n"
                    f"--- [END {label.split(':')[0].strip()}] ---"
                )

            # 3. Prior deliberation turns (if round > 1)
            if deliberation_turns:
                sections.append("## Prior Deliberation Exchanges")
                for turn in deliberation_turns:
                    round_n = turn.metadata.get("round", "?")
                    sections.append(
                        f"--- [Round {round_n}: {turn.agent_role}] ---\n"
                        f"{turn.content}\n"
                        f"--- [END Round {round_n}] ---"
                    )

            # 4. Score diversity check
            _tmp_phase = PhaseResult(
                phase_name="_diversity_check", mode="parallel",
                outputs=list(independent_reviews.values()),
            )
            extracted_scores = _extract_merit_scores(_tmp_phase)
            unique_scores = set(extracted_scores)
            if len(extracted_scores) >= 3 and len(unique_scores) == 1:
                unanimous_score = int(unique_scores.pop())
                sections.append(
                    f"## Score Diversity Alert\n\n"
                    f"**All {len(extracted_scores)} reviewers independently "
                    f"assigned the same merit score ({unanimous_score}).** "
                    f"Unanimous agreement before deliberation is statistically "
                    f"unusual and may indicate anchoring bias rather than genuine "
                    f"consensus. Before confirming your score, actively consider "
                    f"whether the paper deserves a score one point higher or lower "
                    f"than the current unanimous value. Identify the strongest "
                    f"argument for each direction."
                )

            # 5. Deliberation instructions
            peer_roles = [
                r.agent_role for aid, r in independent_reviews.items()
                if aid != current_agent_id
            ]
            peer_list = ", ".join(peer_roles) if peer_roles else "your co-reviewers"
            deliberation_contract = (
                "\n\nReturn a single JSON object. In addition to the normal "
                "review fields, include these deliberation-specific top-level "
                "fields so the meta-reviewer can audit the panel discussion:\n"
                "- `deliberation_position`: your current accept/reject lean "
                "after reading the panel.\n"
                "- `peer_engagement`: a list of named peer claims you agree "
                "with, disagree with, or modify, each with manuscript or graph "
                "evidence and decision impact.\n"
                "- `score_update`: `{previous_score, current_score, changed, "
                "reason}`.\n"
                "- `unresolved_disagreements`: the remaining issues, who "
                "disagrees, and what evidence would resolve them.\n"
                "Keep the reasoning concise and evidence-facing. Do not include "
                "hidden chain-of-thought."
            )

            if round_num == 0:
                delib_instructions = (
                    f"## Deliberation Task (Round {round_num + 1})\n\n"
                    f"You are the **{current_role}**. The other panel members "
                    f"are: {peer_list}.\n\n"
                    f"This is a committee discussion, not a poll. Your job is "
                    f"to help the panel reach a well-reasoned collective judgment "
                    f"by contributing your unique perspective and engaging seriously "
                    f"with your peers' reasoning.\n\n"
                    f"### What to do\n\n"
                    f"**1. Respond to specific peers by name.** Do not just restate "
                    f"your own review. Address what other reviewers said. When the "
                    f"Technical Reviewer flags a methodology gap, the Novelty Reviewer "
                    f"should weigh in on whether that gap also affects the novelty "
                    f"claim. When the Skeptic raises a concern, others should either "
                    f"supply manuscript evidence that mitigates it or explain why it "
                    f"matters more (or less) than the Skeptic claims. If there are "
                    f"at least two peers, engage at least two distinct peer claims.\n\n"
                    f"**2. Build arguments together.** The best committee discussions "
                    f"produce insights no single reviewer had alone. Connect "
                    f"observations across reviews: if two reviewers noticed related "
                    f"problems from different angles, synthesize them into a stronger "
                    f"joint observation. If a strength identified by one reviewer "
                    f"partly mitigates a weakness flagged by another, say so explicitly.\n\n"
                    f"**3. Contribute new observations.** Reading your peers' reviews "
                    f"may prompt you to notice something you missed, or to re-examine "
                    f"a section of the paper you initially skimmed. If a peer's "
                    f"criticism sends you back to the manuscript and you find "
                    f"supporting or contradicting evidence, report it.\n\n"
                    f"**4. Disagree constructively when warranted.** If you believe "
                    f"a peer is wrong, say so with evidence. Do not flatten your "
                    f"assessment to match the group. A split panel with clearly "
                    f"articulated reasons is more useful to the meta-reviewer than "
                    f"artificial unanimity.\n\n"
                    f"**5. Identify the key decision points.** What are the 2-3 "
                    f"questions whose answers determine whether this paper should "
                    f"be accepted? Frame them clearly for the meta-reviewer.\n\n"
                    f"**6. Update your score if warranted.** If your peers' arguments "
                    f"genuinely changed your assessment, update your merit score and "
                    f"explain what convinced you. If not, hold your ground and "
                    f"explain why. Name the strongest opposing argument even when "
                    f"you ultimately reject it.\n\n"
                    f"### What NOT to do\n\n"
                    f"- Do not simply say \"I agree with the Technical Reviewer.\" "
                    f"Explain what you agree with and why it matters from your "
                    f"perspective.\n"
                    f"- Do not restate your entire independent review. Focus on "
                    f"what changed, what was reinforced, and what new insights "
                    f"emerged from reading your peers.\n"
                    f"- Do not default to the lowest score in the panel. Convergence "
                    f"toward rejection is not rigor.\n\n"
                    f"Return your deliberation response as a JSON object matching "
                    f"the same output contract as your independent review."
                    f"{deliberation_contract}"
                )
            else:
                delib_instructions = (
                    f"## Deliberation Task (Round {round_num + 1})\n\n"
                    f"You are the **{current_role}**. This is round "
                    f"{round_num + 1} of deliberation.\n\n"
                    f"Review the prior deliberation exchanges above. At this "
                    f"stage, focus on:\n\n"
                    f"1. **Resolving open questions** from the previous round. "
                    f"If a peer asked you to check something in the manuscript, "
                    f"report what you found. Answer at least one concrete peer "
                    f"challenge unless no peer challenged your position.\n\n"
                    f"2. **Narrowing the key decision points.** The meta-reviewer "
                    f"needs to know: what are the 1-2 issues the committee "
                    f"considers most important, and where does the panel stand "
                    f"on each? Preserve real disagreement instead of forcing "
                    f"consensus.\n\n"
                    f"3. **Finalizing your score.** State your final merit score "
                    f"with a one-sentence rationale that references the "
                    f"deliberation, not just your original review. If your score "
                    f"changed, identify the peer argument or manuscript evidence "
                    f"that changed it. If it did not, explain why the best opposing "
                    f"argument was insufficient.\n\n"
                    f"Keep this response focused and concise. The meta-reviewer "
                    f"will read everything.\n\n"
                    f"Return your response as a JSON object matching "
                    f"the same output contract as your independent review."
                    f"{deliberation_contract}"
                )

            sections.append(delib_instructions)

            return "\n\n".join(sections)

        # Track previous round output for duplicate detection
        prev_round_outputs: dict[str, str] = {}

        for round_num in range(rules.max_rounds):
            if on_event:
                on_event("round_start", {"round": round_num + 1})

            for agent in agents:
                prompt = _build_deliberation_prompt(
                    agent.agent_id, agent.role, round_num,
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
                            context, msg, include_history=False,
                            on_token=lambda chunk, aid=agent.agent_id, r=round_num + 1: on_event(
                                "token", {"agent_id": aid, "role": agent.role, "round": r, "chunk": chunk}
                            ),
                        )
                    else:
                        response = await agent.process(
                            context, msg, include_history=False,
                        )
                except Exception as exc:
                    error = sanitize_error_message(exc)
                    logger.warning(
                        "Agent %s (%s) failed in round %d: %s (retrying once)",
                        agent.agent_id, agent.role, round_num + 1, error,
                    )
                    try:
                        if stream and on_event:
                            response = await agent.process_stream(
                                context, msg, include_history=False,
                                on_token=lambda chunk, aid=agent.agent_id, r=round_num + 1: on_event(
                                    "token", {"agent_id": aid, "role": agent.role, "round": r, "chunk": chunk}
                                ),
                            )
                        else:
                            response = await agent.process(
                                context, msg, include_history=False,
                            )
                    except Exception as retry_exc:
                        error = sanitize_error_message(retry_exc)
                        logger.error(
                            "Agent %s (%s) retry failed in round %d: %s",
                            agent.agent_id, agent.role, round_num + 1, error,
                        )
                        if on_event:
                            on_event("agent_error", {
                                "agent_id": agent.agent_id,
                                "role": agent.role,
                                "round": round_num + 1,
                                "error": error,
                            })
                        result.failed_agents.append({
                            "agent_id": agent.agent_id,
                            "role": agent.role,
                            "round": round_num + 1,
                            "error": error,
                        })
                        continue

                context.add_message(response)
                result.messages.append(response)

                # Detect near-duplicate outputs across agents in same round
                content_trimmed = response.content.strip()
                for other_aid, other_text in prev_round_outputs.items():
                    if other_aid != agent.agent_id and other_text:
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

                round_id = f"round-{round_num + 1}"
                turn_index = len(deliberation_turns) + 1
                output = AgentOutput(
                    agent_id=agent.agent_id,
                    agent_role=agent.role,
                    content=response.content,
                    structured=_try_extract_json(response.content),
                    metadata={
                        **response.metadata,
                        "round": round_num + 1,
                        "round_id": round_id,
                        "speaker_id": agent.agent_id,
                        "speaker_role": agent.role,
                        "deliberation_turn": turn_index,
                    },
                )
                context.add_output(output)
                result.outputs.append(output)
                deliberation_turns.append(output)

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
        result_metadata: dict[str, object] = {
            "configured_deliberation_rounds": rules.max_rounds,
            "effective_deliberation_rounds": rules.max_rounds,
            "deliberation_round_policy": "configured",
            "deliberation_stop_reason": "not_started",
        }

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
                result_metadata["deliberation_stop_reason"] = "no_successful_reviewers"
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
                effective_rounds = (
                    max(2, rules.max_rounds)
                    if len(deliberation_reviewers) > 1
                    else rules.max_rounds
                )
                if effective_rounds != rules.max_rounds:
                    result_metadata["deliberation_round_policy"] = "raised_to_minimum_two_round_pc_panel"
                merit_scores = _extract_merit_scores(phase1)
                if len(merit_scores) >= 2:
                    score_spread = max(merit_scores) - min(merit_scores)
                    if score_spread <= 1.0:
                        logger.info(
                            "[Kernel] Consensus achieved (spread=%.1f, scores=%s). "
                            "Preserving configured deliberation depth: %d rounds.",
                            score_spread, merit_scores, effective_rounds,
                        )
                        if result_metadata["deliberation_round_policy"] == "configured":
                            result_metadata["deliberation_round_policy"] = "configured_preserved_low_score_spread"
                        if on_event:
                            on_event("consensus_detected", {
                                "spread": score_spread,
                                "scores": merit_scores,
                                "effective_rounds": effective_rounds,
                                "reason": "score spread is low, but configured rounds are preserved for review quality",
                            })
                    elif score_spread >= 2.0:
                        effective_rounds = min(max(effective_rounds, 3), 4)
                        result_metadata["deliberation_round_policy"] = "deepened_high_score_spread"
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
                    result_metadata["score_spread"] = score_spread
                    result_metadata["independent_review_scores"] = merit_scores
                result_metadata["effective_deliberation_rounds"] = effective_rounds

                adjusted_rules = DeliberationRules(
                    max_rounds=effective_rounds,
                    timeout_seconds=rules.timeout_seconds,
                    visibility=rules.visibility,
                )

                if on_event:
                    on_event("phase_start", {"phase": "deliberation"})
                phase2 = await self._round_robin.execute(
                    deliberation_reviewers, context, adjusted_rules, on_event,
                    stream=stream,
                    paper_context=user_message.content if user_message else "",
                )
                phase2.phase_name = "deliberation"
                phases.append(phase2)
                result_metadata["deliberation_stop_reason"] = "completed_effective_rounds"
        else:
            result_metadata["deliberation_stop_reason"] = "disabled_by_config"

        # Phase 3: Meta-review (synthesis)
        if on_event:
            on_event("phase_start", {"phase": "meta_review"})

        # Build synthesis prompt from all prior outputs
        all_outputs = []
        for phase in phases:
            for o in phase.outputs:
                all_outputs.append(f"[{o.agent_role}]: {o.content}")

        # Include the FULL original paper context (manuscript + graph summary)
        # so the meta-reviewer can fact-check reviewer claims against the source.
        # Without the paper, the meta-reviewer can only parrot reviewer opinions
        # and cannot detect hallucinated scores or fabricated claims.
        original_context = user_message.content if user_message else ""
        context_block = ""
        if original_context:
            context_block = (
                "\n\n" + "=" * 60
                + "\nORIGINAL MANUSCRIPT AND GRAPH (for fact-checking reviewer claims)\n"
                + "=" * 60 + "\n\n"
                + original_context
            )

        synthesis_prompt = Message(
            role="user",
            content=(
                "Below are all reviewer outputs and deliberation messages. "
                "Synthesize them into the final structured review required by "
                "your system prompt.\n\n"
                "IMPORTANT: You have the full manuscript below. Verify reviewer claims "
                "against the actual text. If a reviewer cites a section/figure/table, "
                "check whether it says what they claim. Flag any reviewer who scores "
                "inconsistently with their own stated weaknesses.\n\n"
                "DELIBERATION AUDIT: Treat the deliberation transcript as PC-panel "
                "evidence, not as noise. Identify which reviewers directly engaged "
                "peer claims, which disagreements were resolved by evidence, which "
                "disagreements remain, and whether any score moved because of the "
                "discussion. The final review should reflect that debate in "
                "`disagreements`, `decision_risk_notes`, `comments_for_pc`, and "
                "the final recommendation rationale. Do not hide a real split panel "
                "behind bland consensus language.\n\n"
                "OFFLINE REVIEW OUTPUT: Populate every `final_review` field needed "
                "for the SC Linklings offline form. ProtoNeo will deterministically "
                "render the exact `.txt` template from those fields, so do not leave "
                "offline-review dimensions blank.\n\n"
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
            metadata=result_metadata,
        )
