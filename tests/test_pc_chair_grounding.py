"""PC Chair grounding: enriched context, query_graph tool loop, proof surfacing."""

import json
from types import SimpleNamespace

import pytest

from apps.paper_review import api
from protoneo.knowledge.graph import KnowledgeGraph


def _graph_dump() -> dict:
    g = KnowledgeGraph(paper_title="VisionHPC", section_names=["II", "V"])
    paper = g.add_node("VisionHPC", "Paper", node_id="paper-root")
    method = g.add_node("VisionHPC workflow", "Method", source_section="II")
    baseline = g.add_node("cuBLAS", "Baseline", source_section="V")
    workload = g.add_node("matrix multiplication", "Workload", source_section="V")
    result = g.add_node("VisionHPC beats cuBLAS by 2x", "Result", source_section="V")
    claim = g.add_node("VisionHPC achieves 2x speedup", "Claim", source_section="V")
    g.add_edge(paper.id, method.id, "HAS_SECTION")
    g.add_edge(method.id, baseline.id, "COMPARED_AGAINST")
    g.add_edge(method.id, workload.id, "EVALUATES_ON")
    g.add_edge(claim.id, result.id, "SUPPORTED_BY")
    g.update_stats()
    return g.model_dump(mode="json")


def _session():
    return SimpleNamespace(
        config={
            "metadata": {"conference": "sc26", "paper_title": "VisionHPC"},
            "agents": {"meta": {"model": "mini/reviewer"}},
        },
        result={
            "phases": [
                {
                    "phase_name": "independent_review",
                    "outputs": [
                        {"agent_role": "Technical Reviewer", "content": "Methodology is sound."},
                    ],
                },
                {
                    "phase_name": "deliberation",
                    "outputs": [
                        {
                            "agent_role": "Skeptic",
                            "content": "Was cuBLAS tuned?",
                            "metadata": {"round": 1},
                        },
                    ],
                },
            ],
            "final_review": {
                "overall_merit": {"score": 3, "label": "Borderline", "rationale": "Mixed."},
                "comments_for_authors": "Clarify the evaluation.",
            },
        },
        app_data={},
        document_markdown="## Paper\nSection V compares VisionHPC against cuBLAS.",
        document_text="Section V compares VisionHPC against cuBLAS.",
        knowledge_graph=_graph_dump(),
        current_stage="post_review",
    )


class _FakeSessionManager:
    def __init__(self, session):
        self.session = session
        self.updated = False

    async def get(self, session_id):
        return self.session if session_id == "session-1" else None

    async def update(self, session):
        self.session = session
        self.updated = True


class _SeqLLMClient:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        idx = min(len(self.calls) - 1, len(self.payloads) - 1)
        return SimpleNamespace(content=json.dumps(self.payloads[idx]))


def test_build_review_context_includes_transcripts_and_graph_evidence():
    context = api._build_review_context(_session())
    assert "Independent Reviews" in context
    assert "Deliberation Transcript" in context
    assert "Knowledge Graph Evidence" in context
    assert "Current Final Review" in context
    assert "Section V compares VisionHPC" in context  # paper markdown present


@pytest.mark.asyncio
async def test_pc_chair_runs_query_graph_tool_loop_and_surfaces_proof(monkeypatch):
    session = _session()
    manager = _FakeSessionManager(session)
    llm = _SeqLLMClient(
        [
            {
                "reply": "Let me check the baselines.",
                "edit_summary": [],
                "final_review_patch": {},
                "tool_calls": [{"query_type": "baselines"}],
                "needs_user_decision": False,
            },
            {
                "reply": "cuBLAS is the compared baseline; the comparison is in Section V.",
                "edit_summary": [],
                "final_review_patch": {},
                "citations": [{"section": "V", "claim": "baseline comparison"}],
                "needs_user_decision": False,
            },
        ]
    )
    monkeypatch.setattr(api, "get_session_manager", lambda: manager)
    monkeypatch.setattr(api, "get_llm_client", lambda: llm)

    response = await api.pc_chair_chat(
        "session-1",
        api.PCChairChatRequest(
            message="Which baseline does the paper actually compare against?",
            current_review=session.result["final_review"],
            apply_edits=False,
            user_role="human_reviewer",
        ),
    )

    # Tool loop triggered a second model call.
    assert len(llm.calls) == 2
    # Grounded context reached the model.
    system_prompt = llm.calls[0]["messages"][0]["content"]
    assert "Deliberation Transcript" in system_prompt
    assert "Knowledge Graph Evidence" in system_prompt
    # Deterministic graph proof is surfaced, not discarded.
    assert response["tool_results"]
    assert response["tool_results"][0]["query_type"] == "baselines"
    graph_citations = [c for c in response["citations"] if c.get("source") == "query_graph"]
    assert graph_citations
    assert "cuBLAS" in graph_citations[0]["summary"] or graph_citations[0]["count"] >= 1


@pytest.mark.asyncio
async def test_pc_chair_no_tool_calls_makes_single_call(monkeypatch):
    session = _session()
    manager = _FakeSessionManager(session)
    llm = _SeqLLMClient(
        [
            {
                "reply": "No graph lookup needed.",
                "edit_summary": [],
                "final_review_patch": {},
                "needs_user_decision": False,
            }
        ]
    )
    monkeypatch.setattr(api, "get_session_manager", lambda: manager)
    monkeypatch.setattr(api, "get_llm_client", lambda: llm)

    response = await api.pc_chair_chat(
        "session-1",
        api.PCChairChatRequest(message="Summarize the decision.", apply_edits=False),
    )
    assert len(llm.calls) == 1
    assert response["tool_results"] == []
