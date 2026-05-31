from protoneo.knowledge.graph import KnowledgeGraph
from protoneo.knowledge.parser import extract_markdown_table_records


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


def test_ingest_visual_evidence_uses_source_text_fallback():
    g = KnowledgeGraph()
    g.add_node("P", "Paper", node_id="paper-root")
    figs = _figs()
    figs[0]["caption"] = ""
    figs[0]["source_text"] = ""
    g.ingest_visual_evidence(figs, tables=[])
    fig = [node for node in g.nodes if node.node_type == "Figure"][0]
    assert fig.source_text == "Extracted figure evidence from page 3"


def test_prune_ungrounded_preserves_extracted_figures():
    g = KnowledgeGraph()
    g.add_node("P", "Paper", node_id="paper-root")
    figs = _figs()
    figs[0]["description"] = ""
    figs[0]["grounding"] = "extracted_no_vlm"
    g.ingest_visual_evidence(figs, tables=[])
    assert g.prune_ungrounded(threshold=0.3) == 0
    assert any(node.node_type == "Figure" for node in g.nodes)


def test_ingest_visual_evidence_backfills_existing_generic_node():
    g = KnowledgeGraph()
    g.add_node("P", "Paper", node_id="paper-root")
    g.add_node("Figure 1: Speedup vs baselines", "Concept")
    g.ingest_visual_evidence(_figs(), tables=[])
    fig = [node for node in g.nodes if node.node_type == "Figure"][0]
    assert fig.attributes["image_path"] == "/u/f1.png"
    assert fig.source_text == "Speedup vs baselines"


def test_markdown_tables_become_first_class_table_records():
    records = extract_markdown_table_records(
        "TABLE I: Runtime settings\n\n"
        "| Platform | Workers |\n"
        "|----------|---------|\n"
        "| GH200    | 4       |\n"
    )
    assert len(records) == 1
    assert records[0]["kind"] == "table"
    assert records[0]["grounding"] == "text_table"
    assert "GH200" in records[0]["source_text"]


def test_ingest_visual_evidence_preserves_text_table():
    g = KnowledgeGraph()
    g.add_node("P", "Paper", node_id="paper-root")
    tables = extract_markdown_table_records(
        "| Platform | Workers |\n|----------|---------|\n| GH200 | 4 |\n"
    )
    g.ingest_visual_evidence([], tables)
    table = [node for node in g.nodes if node.node_type == "Table"][0]
    assert table.source_text
    assert table.attributes["grounding"] == "text_table"


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
