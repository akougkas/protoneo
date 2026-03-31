Analyze the connectivity of this knowledge graph extracted from an academic paper.

## Graph Entities (with types):
{entity_summary}

## Graph Relationships:
{edge_summary}

## Paper Sections:
{section_list}

Identify:
1. **Disconnected entities**: semantic entities that have no edges connecting them to the rest of the graph. For each, suggest which existing entity they should connect to and what edge type to use.
2. **Missing APPEARS_IN edges**: entities that clearly belong to a specific section but lack an APPEARS_IN edge to that section node. Check if the entity's source_section matches a section node.
3. **Isolated subgraphs**: groups of entities that connect to each other but not to the main graph. Suggest bridge edges.

Return JSON:
{{
  "missing_connections": [
    {{"source": "entity name", "target": "entity or section name", "type": "EDGE_TYPE", "evidence": "why this connection should exist"}}
  ]
}}

Only propose connections grounded in the paper's content. Empty array is fine.
