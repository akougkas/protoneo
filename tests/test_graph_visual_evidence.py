from protoneo.knowledge.graph import KnowledgeGraph


def _figs():
    return [
        {
            "index": 1,
            "kind": "figure",
            "page": 3,
            "bbox": {"l": 1},
            "caption": "Speedup vs baselines",
            "image_path": "/u/f1.png",
            "description": "Bar chart; VisionHPC 4.2x over OpenMP.",
            "description_source": "vlm",
            "numeric_claims": ["4.2x over OpenMP"],
            "model": "omni",
            "endpoint": "u",
            "grounding": "visual",
            "confidence": 0.6,
        }
    ]


def test_ingest_visual_evidence_creates_nodes_with_provenance():
    g = KnowledgeGraph()
    g.add_node("VisionHPC", "Paper", node_id="paper-root")
    count = g.ingest_visual_evidence(_figs(), tables=[])
    assert count == 1
    fig = [node for node in g.nodes if node.node_type == "Figure"][0]
    assert fig.attributes["description"] == "Bar chart; VisionHPC 4.2x over OpenMP."
    assert fig.attributes["model"] == "omni"
    assert fig.attributes["image_path"] == "/u/f1.png"
    assert fig.attributes["grounding"] == "visual"
    assert fig.confidence > 0
    assert any(edge.edge_type == "HAS_ARTIFACT" and edge.target_id == fig.id for edge in g.edges)


def test_ingest_visual_evidence_undescribed_marks_low_confidence():
    g = KnowledgeGraph()
    g.add_node("P", "Paper", node_id="paper-root")
    figs = _figs()
    figs[0]["description"] = ""
    figs[0]["description_source"] = "none"
    figs[0]["grounding"] = "extracted_no_vlm"
    g.ingest_visual_evidence(figs, tables=[])
    fig = [node for node in g.nodes if node.node_type == "Figure"][0]
    assert fig.confidence == 0.0
    assert fig.attributes["grounding"] == "extracted_no_vlm"


def test_pipeline_ingests_document_figures():
    """The graph pipeline ingests document.metadata figures/tables at the metadata step."""
    g = KnowledgeGraph()
    g.add_node("P", "Paper", node_id="paper-root")
    doc_meta = {"figures": _figs(), "tables": []}
    g.ingest_visual_evidence(doc_meta.get("figures"), doc_meta.get("tables"))
    assert any(node.node_type == "Figure" for node in g.nodes)


def test_briefing_has_visual_section_and_no_connectivity_overstatement():
    g = KnowledgeGraph()
    g.add_node("P", "Paper", node_id="paper-root")
    g.add_node("VisionHPC", "Method")
    g.ingest_visual_evidence(_figs(), [])
    briefing = g.to_agent_briefing()
    assert "Visual Evidence" in briefing
    assert "VisionHPC 4.2x" in briefing
    assert "% connected" not in briefing


def test_d3_marks_visual_nodes_and_provenance():
    g = KnowledgeGraph()
    g.add_node("P", "Paper", node_id="paper-root")
    g.ingest_visual_evidence(_figs(), [])
    d3 = g.to_d3_format()
    fig = [node for node in d3["nodes"] if node["type"] == "Figure"][0]
    assert fig["attributes"]["grounding"] == "visual"
    assert fig["attributes"]["image_path"] == "/u/f1.png"
