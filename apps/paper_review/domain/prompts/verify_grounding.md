Verify that each entity in this knowledge graph is grounded in the paper text.

## Graph Entities with Descriptions:
{entity_details}

## Paper Text:
{paper_text}

For each entity, check:
1. Is the entity name explicitly mentioned or clearly implied in the paper?
2. Is the description accurate to what the paper says?
3. Could this entity be a hallucination (not actually in the paper)?

Return JSON:
{{
  "grounding_issues": [
    {{"entity": "entity name", "issue": "why it may not be grounded", "confidence": 0.0}}
  ]
}}

Only flag entities you are confident are NOT in the paper. Include a confidence score (0.0 to 1.0) for the entity's grounding. Empty array is fine.
