from apps.paper_review.pipeline import _build_enriched_review_message, _build_visual_evidence_ledger
from protoneo.knowledge.graph import KnowledgeGraph


def _g():
    graph = KnowledgeGraph()
    graph.add_node("P", "Paper", node_id="paper-root")
    graph.ingest_visual_evidence(
        [
            {
                "index": 1,
                "kind": "figure",
                "page": 3,
                "bbox": {},
                "caption": "Speedup",
                "image_path": "/f1.png",
                "description": "Bar chart; 4.2x over OpenMP.",
                "description_source": "vlm",
                "numeric_claims": ["4.2x over OpenMP"],
                "model": "omni",
                "endpoint": "u",
                "grounding": "visual",
                "confidence": 0.6,
            }
        ],
        [],
    )
    return graph


def test_ledger_lists_described_artifacts_only():
    ledger = _build_visual_evidence_ledger(_g())
    assert "Figure 1" in ledger
    assert "4.2x over OpenMP" in ledger
    assert "image_path" not in ledger


def test_enriched_message_includes_ledger():
    message = _build_enriched_review_message("USER MSG", _g())
    assert "USER MSG" in message
    assert "Visual Evidence Ledger" in message
    assert "4.2x over OpenMP" in message
