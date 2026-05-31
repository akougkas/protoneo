from apps.paper_review.pipeline import review_graph_quality
from protoneo.knowledge.graph import KnowledgeGraph


def _graph_with_visual(described: int, total: int) -> KnowledgeGraph:
    graph = KnowledgeGraph()
    graph.add_node("P", "Paper", node_id="paper-root")
    figures = []
    for i in range(total):
        figures.append({
            "index": i + 1,
            "kind": "figure",
            "page": 1,
            "bbox": {},
            "caption": f"Fig {i + 1}",
            "image_path": f"/f{i}.png",
            "description": "x" if i < described else "",
            "description_source": "vlm" if i < described else "none",
            "numeric_claims": [],
            "model": "omni",
            "endpoint": "u",
            "grounding": "visual" if i < described else "extracted_no_vlm",
            "confidence": 0.6 if i < described else 0.0,
        })
    graph.ingest_visual_evidence(figures, [])
    return graph


def test_quality_reports_grounding_mode():
    assert review_graph_quality(_graph_with_visual(0, 3))["grounding_mode"] == "text_only"
    assert review_graph_quality(_graph_with_visual(3, 3))["grounding_mode"] == "vision_grounded"
    assert review_graph_quality(_graph_with_visual(1, 3))["grounding_mode"] == "mixed"
    quality = review_graph_quality(_graph_with_visual(2, 3))
    assert quality["visual_evidence_count"] == 3
    assert quality["described_artifact_count"] == 2
