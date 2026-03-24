"""Model capability benchmark for ProtoNeo.

Scores 5 dimensions relevant to multi-agent academic review:
1. JSON Compliance (0-20): Structured output with schema adherence
2. Review Depth (0-20): Methodological flaw detection and critique quality
3. Reasoning Chain (0-20): Multi-step logical analysis with correct conclusion
4. Context Utilization (0-20): Extracting buried details from a dense passage
5. Instruction Following (0-20): Adhering to exact formatting constraints

Scoring uses synonym families and structural analysis instead of exact
keyword matching. Each sub-criterion has documented point values. All
providers run in parallel with proper warm-up.
"""

import asyncio
import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger("protoneo.llm.benchmark")

_REASONING_MODEL_HINTS = (
    "reasoning", "thinking", "qwen3.5", "qwen35", "qwen3", "-i1",
    "o1", "o3", "o4", "deepseek-r1",
)


# ── Dimension 1: JSON Compliance ─────────────────────────────
#
# Tests whether the model can produce valid, schema-compliant JSON
# without extraneous text. This is critical for the review pipeline
# which parses structured agent outputs.

_JSON_PROMPT = """\
Return a JSON object with exactly this schema. No other text, no markdown fences.

{"paper_title": "<string>", "scores": {"novelty": <int 1-5>, "rigor": <int 1-5>, "clarity": <int 1-5>}, "verdict": "<accept|reject|revise>", "key_issues": ["<string>", "<string>"]}

Paper: A distributed sorting algorithm achieving 2.3x speedup over RadixSort-MPI on 65K nodes with O(n/p log p) communication complexity."""


def _extract_json(content: str) -> tuple[Any | None, bool]:
    """Extract a JSON object from model output.

    Returns (parsed_object, was_clean) where was_clean is True if the
    output was pure JSON without wrapper text or code fences.
    """
    cleaned = content.strip()

    # Try raw parse first (cleanest output)
    try:
        return json.loads(cleaned), True
    except json.JSONDecodeError:
        pass

    # Strip markdown code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", cleaned, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip()), False
        except json.JSONDecodeError:
            pass

    # Extract first complete JSON object from mixed text
    brace_start = cleaned.find("{")
    if brace_start >= 0:
        depth = 0
        for i in range(brace_start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[brace_start:i + 1]), False
                    except json.JSONDecodeError:
                        break

    return None, False


def _score_json_compliance(content: str) -> tuple[int, dict]:
    """Score JSON compliance (0-20).

    Rubric:
      Valid JSON parsed cleanly (no fences/wrapper):  8 pts
      Valid JSON but needed extraction:               5 pts
      All 4 required keys present:                    4 pts
      scores sub-object has 3 numeric keys:           3 pts
      verdict is one of accept/reject/revise:         2 pts
      key_issues is a non-empty list of strings:      2 pts
      No text outside the JSON object:                1 pt
    """
    details = {
        "valid_json": False, "clean_output": False,
        "has_all_keys": False, "scores_valid": False,
        "verdict_valid": False, "issues_valid": False,
    }

    data, was_clean = _extract_json(content)
    if data is None:
        return 0, details

    score = 0
    details["valid_json"] = True

    if was_clean:
        details["clean_output"] = True
        score += 8
    else:
        score += 5

    if not isinstance(data, dict):
        return score, details

    # Required top-level keys
    required = {"paper_title", "scores", "verdict", "key_issues"}
    if required.issubset(data.keys()):
        details["has_all_keys"] = True
        score += 4

    # scores sub-object: 3 numeric fields in 1-5 range
    scores_obj = data.get("scores", {})
    if isinstance(scores_obj, dict):
        numeric_keys = [
            k for k in ("novelty", "rigor", "clarity")
            if isinstance(scores_obj.get(k), (int, float))
        ]
        if len(numeric_keys) == 3:
            in_range = all(1 <= scores_obj[k] <= 5 for k in numeric_keys)
            if in_range:
                details["scores_valid"] = True
                score += 3
            else:
                score += 1  # Partial: right type, wrong range

    # verdict
    verdict = str(data.get("verdict", "")).lower().strip()
    if verdict in ("accept", "reject", "revise"):
        details["verdict_valid"] = True
        score += 2

    # key_issues: non-empty list of strings
    issues = data.get("key_issues", [])
    if isinstance(issues, list) and len(issues) >= 2 and all(isinstance(i, str) for i in issues):
        details["issues_valid"] = True
        score += 2
    elif isinstance(issues, list) and len(issues) >= 1:
        score += 1

    # Clean output bonus
    stripped = content.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        score += 1

    return min(score, 20), details


# ── Dimension 2: Review Depth ────────────────────────────────
#
# Tests whether the model can identify a specific methodological flaw
# and explain why it undermines the paper's claims. The flaw is running
# each experiment only once with no repetition or statistical analysis.

_DEPTH_PROMPT = """\
The following paper excerpt describes an evaluation methodology. Identify the specific methodological flaw and explain why it undermines the paper's claims.

"We evaluated ScaleSort on the Summit supercomputer using 1024, 4096, and 65536 nodes. Each experiment sorted 1TB of random 64-bit integers. We measured throughput in GB/s and compared against RadixSort-MPI. ScaleSort achieved 2.3x higher throughput at 65536 nodes. We ran each configuration once to minimize cluster allocation costs."

Respond with exactly 3-5 sentences identifying the flaw."""

# Synonym families for the single-run flaw. Each group captures one way
# of expressing the core issue. A model only needs to match ONE phrase
# from ANY group to get credit for identifying the flaw.
_FLAW_SYNONYMS = [
    # Direct references to single execution
    "once", "single run", "one run", "single trial", "single execution",
    "single measurement", "ran once", "run once", "executed once",
    "one trial", "one time", "one experiment", "single experiment",
    # Lack of repetition/replication
    "no repetit", "no repeat", "not repeated", "not replicated",
    "without repetit", "without repeat", "lack of repetit", "lack of replicat",
    "no replicat", "unreplicated", "unrepeated",
    # Statistical insufficiency
    "no statistical", "statistical significance", "statistical valid",
    "no variance", "no standard deviation", "no error bar",
    "no confidence", "confidence interval",
    "cannot determine varia", "cannot assess varia",
    "insufficient sample", "sample size of one", "sample size of 1",
    "n=1", "n = 1",
    # Reproducibility concerns
    "reproducib", "reliab", "not reproducible", "cannot be verified",
    "cannot reproduce",
]

_IMPACT_SYNONYMS = [
    # The flaw undermines the claims
    "undermine", "cannot claim", "cannot conclude", "does not support",
    "insufficient", "unreliable", "invalid", "weaken", "questionable",
    "not justified", "unjustified", "unsubstantiated", "meaningless",
    "not meaningful", "cannot be trusted", "jeopardize", "compromise",
    "calls into question", "cast doubt", "no basis", "premature",
    "flawed", "unsound", "not rigorous",
    # Specific consequences
    "outlier", "anomal", "noise", "fluctuat", "could be due to",
    "might not reflect", "may not generalize",
]


def _score_review_depth(content: str) -> tuple[int, dict]:
    """Score review depth (0-20).

    Rubric:
      Identifies the single-run / no-repetition flaw:   10 pts
      Explains why it undermines the 2.3x claim:         5 pts
      References specific paper details:                  3 pts
      Response is appropriate length (3-8 sentences):     2 pts
    """
    details = {
        "found_flaw": False, "explained_impact": False,
        "specific_references": False, "appropriate_length": False,
    }
    score = 0
    lower = content.lower()

    if any(kw in lower for kw in _FLAW_SYNONYMS):
        details["found_flaw"] = True
        score += 10

    if any(kw in lower for kw in _IMPACT_SYNONYMS):
        details["explained_impact"] = True
        score += 5

    # References specific details from the passage
    specifics = ["65536", "65,536", "throughput", "2.3x", "2.3 x",
                 "radixsort", "radix", "summit", "1tb", "1 tb"]
    if sum(1 for kw in specifics if kw in lower) >= 2:
        details["specific_references"] = True
        score += 3

    # Sentence count (split on sentence-ending punctuation)
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', content.strip()) if s.strip()]
    if 2 <= len(sentences) <= 8:
        details["appropriate_length"] = True
        score += 2

    return min(score, 20), details


# ── Dimension 3: Reasoning Chain ─────────────────────────────
#
# Tests multi-step logical reasoning with a complexity analysis problem.
# The claim is INCORRECT. Total communication per round is O(n), not
# O(n/p), because the coordinator receives n/p from each of p processors
# (total: p * n/p = n) and sends n/p back to each (total: p * n/p = n).
# Over log(p) rounds the total is O(n log p), not O(n/p log p).

_REASONING_PROMPT = """\
A paper claims their distributed algorithm has O(n/p log p) total communication complexity across all processors combined. They present three facts:
1. In each round, each of the p processors sends n/p elements to a single coordinator node.
2. The coordinator merges all received elements and sends n/p elements back to each processor.
3. The algorithm performs log(p) rounds of this coordinate-and-redistribute step.

Based on these three facts alone, is the O(n/p log p) total communication claim correct? Show your reasoning step by step, then state "CORRECT" or "INCORRECT" as the final word."""


def _score_reasoning(content: str) -> tuple[int, dict]:
    """Score reasoning chain (0-20).

    Rubric:
      Shows step-by-step derivation:                    4 pts
      Computes per-round total correctly (O(n)):         4 pts
      Multiplies by log(p) for grand total:              3 pts
      Reaches correct conclusion (INCORRECT):            6 pts
      Clear final statement:                             3 pts
    """
    details = {
        "shows_steps": False, "per_round_correct": False,
        "total_correct": False, "correct_answer": False,
        "clear_conclusion": False,
    }
    score = 0
    lower = content.lower()

    # Reconstruct full reasoning for thinking models
    full_text = _reasoning_view(content, None)
    full_lower = full_text.lower() if full_text != content else lower

    # Step-by-step derivation (needs at least 3 reasoning markers)
    step_markers = [
        "step", "first", "second", "third", "therefore", "thus",
        "combining", "since", "because", "this means", "so the total",
        "per round", "each round", "in total", "summing", "let's",
        "we need to", "consider", "notice", "observe", "calculate",
        "for each round", "across all",
    ]
    if sum(1 for kw in step_markers if kw in full_lower) >= 3:
        details["shows_steps"] = True
        score += 4

    # Per-round total = O(n): recognizes that p * (n/p) = n
    per_round_indicators = [
        "p * n/p", "p*(n/p)", "p × n/p", "p * (n/p)",
        "p times n/p", "p · n/p",
        "n/p from each of p", "n/p from p",
        "n/p * p", "(n/p)*p", "(n/p) * p", "(n/p)p",
        "sums to n", "totals n", "total of n",
        "equals n", "= n", "is n ",
        "coordinator receives n ", "coordinator handles n ",
        "receive n element", "receives n element",
        "n data", "o(n) per round", "o(n) each round",
        "2n per round", "2n each round", "2*n per round",
    ]
    if any(kw in full_lower for kw in per_round_indicators):
        details["per_round_correct"] = True
        score += 4

    # Grand total = O(n log p)
    total_indicators = [
        "n log p", "n*log(p)", "n * log(p)", "n·log(p)",
        "n log(p)", "o(n log p)", "o(n·log p)", "o(nlogp)",
        "n times log p", "n multiplied by log p",
        "total.*n.*log", "overall.*n.*log",
    ]
    if any(re.search(kw, full_lower) for kw in total_indicators):
        details["total_correct"] = True
        score += 3

    # Correct answer: claim is INCORRECT
    # Accept many phrasings, not just the exact word as the last token
    incorrect_indicators = [
        "incorrect", "not correct", "is wrong", "is false",
        "is invalid", "is erroneous", "claim is flawed",
        "claim does not hold", "does not match",
        "should be.*n log p", "should be.*o(n",
        "the correct.*is.*n log", "actually.*n log",
        "overstates", "understates the total",
        "per-processor.*not total", "per processor.*not total",
        "confus.*per.processor.*total",
        "n/p log p.*per processor", "n/p log p.*not.*total",
    ]
    if any(re.search(kw, full_lower) for kw in incorrect_indicators):
        details["correct_answer"] = True
        score += 6

    # Clear final statement (ends with a definitive conclusion)
    last_200 = content.strip()[-200:].lower()
    conclusion_patterns = [
        "incorrect", "not correct", "the claim is wrong",
        "the claim is false", "the claim is invalid",
        "the answer is.*incorrect", "conclusion.*incorrect",
    ]
    if any(re.search(p, last_200) for p in conclusion_patterns):
        details["clear_conclusion"] = True
        score += 3

    return min(score, 20), details


# ── Dimension 4: Context Utilization ─────────────────────────
#
# Tests ability to extract specific details buried in a multi-section
# technical document. The passage is ~3K characters with facts
# distributed across 6 sections.

_CONTEXT_PASSAGE = """\
Section 1: Introduction
Distributed sorting is fundamental to large-scale data processing. Prior work has explored various approaches including sample sort, bitonic sort, and radix-based methods. The challenge of sorting at exascale requires new approaches that minimize inter-node communication.

Section 2: Background
Communication complexity in distributed sorting is measured by the total data volume transferred across the network. For p processors sorting n elements, the theoretical lower bound is Omega(n/p) per processor. Achieving near-linear speedup requires keeping communication within a constant factor of this bound.

Section 3: Design
ScaleSort uses a three-phase approach. Phase 1 performs local sorting using introsort. Phase 2 determines pivot elements through adaptive oversampling with a sample rate of 4*log(p) elements per processor. Phase 3 performs the all-to-all exchange. The critical innovation is the use of Hilbert curve ordering for pivot selection, which reduces worst-case imbalance from O(sqrt(n/p)) to O(log(n/p)).

Section 4: Implementation Details
The implementation uses MPI-3 one-sided communication (MPI_Put) with passive target synchronization. The buffer management strategy pre-allocates 2.5x the expected message size to handle load imbalance without dynamic allocation. The implementation was validated on Summit (IBM POWER9, 4608 nodes), Frontier (AMD EPYC, 9408 nodes), and a 128-node Intel testbed.

Section 5: Evaluation
We compared against three baselines: RadixSort-MPI, HykSort, and AMS-Sort. At 65536 nodes on Frontier, ScaleSort achieved 847 GB/s aggregate throughput. The pivot selection overhead was measured at 3.2% of total runtime. Network utilization peaked at 78% of bisection bandwidth.

Section 6: Limitations
The current implementation requires that n >> p^2 for optimal load balance. For small n/p ratios (below 10^4 elements per processor), the sampling overhead dominates. The Hilbert curve computation adds O(d * log(d)) overhead per element where d is the dimensionality of the sort key."""

_CONTEXT_QUESTION = """\
Based on the paper sections above, answer these three specific questions:
1. What is the exact sample rate used in the adaptive oversampling?
2. On which specific supercomputer was the 847 GB/s throughput measured?
3. What is the buffer pre-allocation factor for load imbalance?

Answer each with the exact value from the text."""


def _score_context_utilization(content: str) -> tuple[int, dict]:
    """Score context utilization (0-20).

    Rubric:
      Q1 correct (sample rate = 4*log(p)):     7 pts (4 partial)
      Q2 correct (Frontier):                   7 pts
      Q3 correct (2.5x):                       6 pts
    """
    details = {"q1_correct": False, "q2_correct": False, "q3_correct": False}
    score = 0
    lower = content.lower()

    # Q1: sample rate = 4*log(p) per processor
    q1_exact = [
        "4*log(p)", "4 * log(p)", "4*log p", "4 log(p)", "4·log",
        "4×log", "4 * log p", "4*log( p)", "4log(p)",
        "four times log(p)", "four times log p",
    ]
    if any(kw in lower for kw in q1_exact):
        details["q1_correct"] = True
        score += 7
    elif "4" in content and "log" in lower:
        # Partial: mentions both 4 and log but not in the right format
        score += 4

    # Q2: Frontier
    if "frontier" in lower:
        details["q2_correct"] = True
        score += 7

    # Q3: 2.5x pre-allocation factor
    q3_patterns = ["2.5x", "2.5 x", "2.5 times", "2.5×", "250%",
                   "two and a half times", "two-and-a-half"]
    if any(kw in lower for kw in q3_patterns):
        details["q3_correct"] = True
        score += 6
    elif "2.5" in content:
        # Partial: mentions 2.5 but not clearly as a factor
        details["q3_correct"] = True
        score += 5

    return min(score, 20), details


# ── Dimension 5: Instruction Following ───────────────────────
#
# Tests whether the model follows exact formatting constraints.
# This matters because the review pipeline parses structured output
# with specific section headers and counts.

_INSTRUCTION_PROMPT = """\
Format your response EXACTLY as specified. No deviations.

VERDICT: [one word: ACCEPT or REJECT]
CONFIDENCE: [integer 1-5]
SUMMARY: [exactly 2 sentences, no more, no less]
REVISION: [bullet list with exactly 3 items, each starting with "- "]

Review this claim: "Our algorithm achieves 2.3x speedup with O(n/p log p) communication."
"""


def _score_instruction_following(content: str) -> tuple[int, dict]:
    """Score instruction following (0-20).

    Rubric:
      VERDICT section with valid value:       4 pts
      CONFIDENCE section with integer 1-5:    4 pts
      SUMMARY section with ~2 sentences:      4 pts
      REVISION section with exactly 3 bullets: 5 pts
      All 4 sections present and correct:      3 pts (bonus)
    """
    details = {
        "has_verdict": False, "has_confidence": False,
        "has_summary": False, "has_revision": False,
        "format_exact": False,
    }
    score = 0
    lines = content.strip().split("\n")

    # Parse sections
    current_section = None
    sections: dict[str, list[str]] = {}

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        upper = stripped.upper()
        for header in ("VERDICT:", "CONFIDENCE:", "SUMMARY:", "REVISION:"):
            if upper.startswith(header):
                current_section = header.rstrip(":")
                value = stripped[len(header):].strip()
                sections[current_section] = [value] if value else []
                break
        else:
            # Continuation line for current section
            if current_section and current_section in sections:
                sections[current_section].append(stripped)

    # VERDICT: must be ACCEPT or REJECT
    verdict_lines = sections.get("VERDICT", [])
    if verdict_lines:
        val = " ".join(verdict_lines).strip().upper()
        if val in ("ACCEPT", "REJECT"):
            details["has_verdict"] = True
            score += 4

    # CONFIDENCE: must be integer 1-5
    confidence_lines = sections.get("CONFIDENCE", [])
    if confidence_lines:
        val = " ".join(confidence_lines).strip()
        try:
            num = int(val)
            if 1 <= num <= 5:
                details["has_confidence"] = True
                score += 4
        except ValueError:
            # Try extracting first integer
            match = re.search(r"\b([1-5])\b", val)
            if match:
                details["has_confidence"] = True
                score += 3  # Minor penalty for extra text

    # SUMMARY: should be exactly 2 sentences
    summary_lines = sections.get("SUMMARY", [])
    if summary_lines:
        summary_text = " ".join(summary_lines).strip()
        # Count sentences by splitting on ". " or "! " or "? " or terminal punctuation at end.
        # Avoids false splits on decimals (2.3x), abbreviations (e.g.), etc.
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', summary_text)
        sentences = [s.strip() for s in sentences if s.strip()]
        if not sentences and summary_text:
            sentences = [summary_text]  # Single sentence without trailing split
        if len(sentences) == 2:
            details["has_summary"] = True
            score += 4
        elif 1 <= len(sentences) <= 3:
            score += 2  # Close but not exact

    # REVISION: exactly 3 bullet items starting with "- "
    revision_lines = sections.get("REVISION", [])
    if revision_lines:
        bullets = [l for l in revision_lines if l.startswith("- ") or l.startswith("* ")]
        if len(bullets) == 3:
            details["has_revision"] = True
            score += 5
        elif len(bullets) == 2:
            score += 3
        elif len(bullets) >= 1:
            score += 1

    # Bonus: all 4 sections present and correct
    all_correct = all([
        details["has_verdict"], details["has_confidence"],
        details["has_summary"], details["has_revision"],
    ])
    if all_correct:
        details["format_exact"] = True
        score += 3

    return min(score, 20), details


# ── Benchmark Runner ─────────────────────────────────────────

_DIMENSIONS = [
    ("json_compliance", "JSON Compliance", _JSON_PROMPT, _score_json_compliance),
    ("review_depth", "Review Depth", _DEPTH_PROMPT, _score_review_depth),
    ("reasoning", "Reasoning Chain", _REASONING_PROMPT, _score_reasoning),
    ("context_utilization", "Context Utilization",
     _CONTEXT_PASSAGE + "\n\n" + _CONTEXT_QUESTION, _score_context_utilization),
    ("instruction_following", "Instruction Following",
     _INSTRUCTION_PROMPT, _score_instruction_following),
]

# Tags derived from dimension scores
_TAG_THRESHOLDS = {
    "json_compliance": (16, "structured"),
    "review_depth": (14, "deep-review"),
    "reasoning": (14, "reasoning"),
    "context_utilization": (16, "long-context"),
    "instruction_following": (16, "precise"),
}

# Role suggestions based on tag combinations
_ROLE_SUGGESTIONS = {
    frozenset(["structured", "deep-review", "reasoning"]): ["Technical Reviewer", "Meta-Reviewer"],
    frozenset(["deep-review", "reasoning"]): ["Technical Reviewer", "Skeptic"],
    frozenset(["structured", "precise"]): ["Clarity Reviewer", "Graph Extractor"],
    frozenset(["reasoning", "long-context"]): ["Novelty Reviewer", "Meta-Reviewer"],
    frozenset(["structured", "long-context"]): ["Ontology Generator", "Meta-Reviewer"],
}


def _supports_reasoning_mode(model_id: str, provider: str, litellm_prefix: str) -> bool:
    """Detect models that benefit from reasoning/thinking tokens."""
    haystack = f"{provider} {model_id} {litellm_prefix}".lower()
    return any(hint in haystack for hint in _REASONING_MODEL_HINTS)


def _benchmark_kwargs(
    model_id: str,
    provider: str,
    api_base: str,
    litellm_prefix: str,
) -> dict[str, Any]:
    """Build extra request kwargs for a benchmark target."""
    kwargs: dict[str, Any] = {}
    if api_base:
        kwargs["api_base"] = api_base
        kwargs["api_key"] = "none"

    is_reasoning = _supports_reasoning_mode(model_id, provider, litellm_prefix)
    if is_reasoning:
        kwargs["reasoning_effort"] = "high"
        kwargs["allowed_openai_params"] = ["reasoning_effort"]
        kwargs["drop_params"] = True

    return kwargs


def _reasoning_view(content: str, response: Any) -> str:
    """Reconstruct full reasoning text including chain-of-thought.

    Reasoning models put their thinking in:
    1. response.raw.choices[0].message.reasoning_content
    2. <think>/<thinking> tags in raw content

    The scorer needs the full text to evaluate reasoning quality.
    """
    if response is None:
        return content

    raw = getattr(response, "raw", None) or {}
    choices = raw.get("choices") or []
    raw_message = (choices[0] if choices else {}).get("message", {})

    # Source 1: LiteLLM-extracted reasoning_content
    reasoning_content = raw_message.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content.strip():
        return f"{reasoning_content.strip()}\n\n{content.strip()}".strip()

    # Source 2: <think>/<thinking> tags in raw content
    raw_content = raw_message.get("content")
    if isinstance(raw_content, str):
        think_match = re.search(
            r"<(?:think|thinking)>([\s\S]*?)</(?:think|thinking)>",
            raw_content,
        )
        if think_match:
            thinking_text = think_match.group(1).strip()
            return f"{thinking_text}\n\n{content.strip()}".strip()

    return content


async def benchmark_model(
    model_id: str,
    llm_client: "LLMClient",
    provider: str = "",
    api_base: str = "",
    litellm_prefix: str = "",
    litellm_model: str = "",
    session_id: str = "benchmark",
    on_dimension: callable = None,
) -> dict[str, Any]:
    """Run the full 5-dimension benchmark on a single model.

    Starts with a warm-up call using a real prompt to load model weights
    into VRAM. Token counts and throughput come from LiteLLM response.usage.
    """
    effective_model = litellm_model or (
        f"{litellm_prefix}{model_id}" if litellm_prefix else model_id
    )
    extra_kwargs = _benchmark_kwargs(model_id, provider, api_base, litellm_prefix)

    logger.info("Benchmarking %s/%s (effective=%s)", provider, model_id, effective_model)

    result = {
        "model_id": model_id,
        "provider": provider,
        "status": "running",
        "dimensions": {},
        "total_score": 0,
        "tags": [],
        "suggested_roles": [],
        "protoneo_class": "",
        "throughput": {
            "total_completion_tokens": 0,
            "total_prompt_tokens": 0,
            "total_latency_seconds": 0,
            "tokens_per_second": 0,
        },
        "error": None,
    }

    # Cloud providers (subscription APIs) don't need warmup. Models are
    # always loaded. Local providers need warmup to load weights into VRAM.
    is_cloud = provider in ("openai",)  # anthropic removed
    if not is_cloud:
        try:
            await llm_client.complete(
                model=effective_model,
                messages=[{"role": "user", "content": "Briefly list 3 strengths of distributed sorting algorithms."}],
                session_id=session_id,
                max_tokens=100,
                **extra_kwargs,
            )
        except Exception as e:
            logger.error("Warm-up failed for %s: %s", model_id, e)
            result["status"] = "error"
            result["error"] = f"Warm-up failed: {e}"
            result["protoneo_class"] = "unreachable"
            return result

    # Run all 5 dimensions
    total_score = 0
    total_completion_tokens = 0
    total_prompt_tokens = 0
    total_latency = 0

    pacing_seconds = 0

    for dim_idx, (dim_key, dim_name, prompt, scorer) in enumerate(_DIMENSIONS):
        if on_dimension:
            on_dimension(dim_key, dim_name)

        if pacing_seconds and dim_idx > 0:
            await asyncio.sleep(pacing_seconds)

        dim_result = {"score": 0, "max": 20, "details": {}, "latency_seconds": 0, "error": None}

        start = time.monotonic()
        try:
            response = await llm_client.complete(
                model=effective_model,
                messages=[{"role": "user", "content": prompt}],
                session_id=session_id,
                **extra_kwargs,
            )
            elapsed = time.monotonic() - start
            dim_result["latency_seconds"] = round(elapsed, 2)

            # For reasoning dimension, include chain-of-thought in scored content
            scored_content = response.content
            if dim_key == "reasoning":
                scored_content = _reasoning_view(response.content, response)

            score, details = scorer(scored_content)
            dim_result["score"] = score
            dim_result["details"] = details

            logger.info(
                "benchmark[%s/%s] score=%d/20 details=%s",
                model_id, dim_key, score, json.dumps(details),
            )

            total_completion_tokens += response.usage.completion_tokens or 0
            total_prompt_tokens += response.usage.prompt_tokens or 0
            total_latency += elapsed

        except Exception as e:
            elapsed = time.monotonic() - start
            dim_result["latency_seconds"] = round(elapsed, 2)
            dim_result["error"] = str(e)
            logger.error("benchmark[%s/%s] ERROR: %s", model_id, dim_key, e)

        total_score += dim_result["score"]
        result["dimensions"][dim_key] = dim_result

    # Aggregate
    result["total_score"] = total_score
    result["throughput"] = {
        "total_completion_tokens": total_completion_tokens,
        "total_prompt_tokens": total_prompt_tokens,
        "total_latency_seconds": round(total_latency, 2),
        "tokens_per_second": (
            round(total_completion_tokens / total_latency, 1) if total_latency > 0 else 0
        ),
    }

    # Derive tags
    tags = []
    for dim_key, (threshold, tag) in _TAG_THRESHOLDS.items():
        dim = result["dimensions"].get(dim_key, {})
        if dim.get("score", 0) >= threshold:
            tags.append(tag)
    result["tags"] = tags

    # Suggest roles
    tag_set = frozenset(tags)
    suggested = []
    for required_tags, roles in _ROLE_SUGGESTIONS.items():
        if required_tags.issubset(tag_set):
            for r in roles:
                if r not in suggested:
                    suggested.append(r)
    result["suggested_roles"] = suggested

    # Classify
    if total_score >= 85:
        result["protoneo_class"] = "excellent"
    elif total_score >= 70:
        result["protoneo_class"] = "good"
    elif total_score >= 50:
        result["protoneo_class"] = "usable"
    elif total_score >= 30:
        result["protoneo_class"] = "limited"
    else:
        result["protoneo_class"] = "unsuitable"

    result["status"] = "complete"
    logger.info(
        "Benchmark done: %s/%s total=%d/100 class=%s tags=%s tps=%.1f",
        provider, model_id, total_score, result["protoneo_class"],
        tags, result["throughput"]["tokens_per_second"],
    )

    return result


async def benchmark_all_parallel(
    targets: list[dict],
    llm_client: "LLMClient",
    on_progress: callable = None,
) -> list[dict[str, Any]]:
    """Benchmark all targets in parallel.

    Each provider runs on separate hardware, so parallelism is free.
    """
    async def _run_one(target: dict) -> dict:
        if on_progress:
            on_progress("start", target["model_id"], target["provider"])

        result = await benchmark_model(
            model_id=target["model_id"],
            llm_client=llm_client,
            provider=target["provider"],
            api_base=target.get("api_base", ""),
            litellm_prefix=target.get("litellm_prefix", ""),
            litellm_model=target.get("litellm_model", ""),
            on_dimension=lambda d, n: on_progress("dimension", target["model_id"], d) if on_progress else None,
        )

        if on_progress:
            on_progress("complete", target["model_id"], target["provider"], result)

        return result

    results = await asyncio.gather(*[_run_one(t) for t in targets], return_exceptions=True)

    final = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            final.append({
                "model_id": targets[i]["model_id"],
                "provider": targets[i]["provider"],
                "status": "error",
                "error": str(r),
                "total_score": 0,
                "protoneo_class": "error",
                "dimensions": {},
                "tags": [],
                "suggested_roles": [],
                "throughput": {},
            })
        else:
            final.append(r)

    return final
