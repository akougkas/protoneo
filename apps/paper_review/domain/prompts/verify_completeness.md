Check this knowledge graph for completeness against the full paper text.

## Current Graph Entities:
{entity_summary}

## Paper Text:
{paper_text}

Identify concrete, named entities discussed in the paper that are MISSING from the graph. Focus on:
- Methods, algorithms, or techniques mentioned by name
- Datasets or benchmarks used for evaluation
- Baselines or competing systems compared against
- Specific quantitative results (numbers, metrics, performance values)
- Hardware platforms, systems, or tools used

Do NOT suggest vague concepts. Every suggestion must be a specific named thing from the paper.

Return JSON:
{{
  "missing_concepts": [
    {{"concept": "exact name from paper", "suggested_type": "Method|Dataset|Baseline|Result|etc", "section": "which section mentions it", "evidence": "quote or paraphrase from paper"}}
  ]
}}
