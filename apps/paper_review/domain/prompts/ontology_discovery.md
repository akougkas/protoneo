Design 3-5 paper-SPECIFIC entity types for reviewer evaluation of this paper.

## Base types (DO NOT duplicate)
Entity: Claim, Method, Dataset, Metric, Baseline, Result, Limitation, Concept, Reference, Equation
Edge: USES, EVALUATES_ON, COMPARED_AGAINST, ACHIEVES, EXTENDS, CITES, PART_OF, CONTRADICTS

{seed_section}
## Instructions

Design types that capture domain concepts the base types miss. Each type needs:
- A clear name (PascalCase, e.g., "SensorType" not "Sensor_Type")
- A description of what it represents and why reviewers care
- 2-3 concrete examples from THIS paper

Also design 2-3 paper-specific edge types not in the base set.

Output JSON:
{{"entity_types": [{{"name": "...", "description": "...", "examples": ["from this paper"]}}], "edge_types": [{{"name": "UPPER_CASE", "description": "...", "source_targets": [{{"source": "Type1", "target": "Type2"}}]}}], "paper_domain": "...", "key_contributions": ["..."], "analysis_summary": "..."}}

## Paper:

{paper_text}
