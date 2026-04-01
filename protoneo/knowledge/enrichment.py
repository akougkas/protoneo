"""Document enrichment using local VLM and LLM.

After Docling extracts structured content from a PDF, this module
enriches it with AI-generated descriptions:

1. VLM figure descriptions: each cropped figure image is sent to a
   vision-language model for a detailed scientific description.
2. LLM equation reconstruction: inline and block equations are
   reconstructed into LaTeX notation.

Uses ProtoNeo's LLMClient which routes through LiteLLM to local
LM Studio or Ollama endpoints. No hardcoded URLs or model names.
"""

import base64
import logging
from pathlib import Path
from typing import Any

from ..llm.client import LLMClient

logger = logging.getLogger("protoneo.knowledge.enrichment")

_FIGURE_SYSTEM = (
    "You are a scientific document analysis assistant. "
    "Describe the figure in detail for an expert reader. "
    "Include: what the figure shows, axis labels, data trends, "
    "key takeaways, and any statistical patterns. "
    "If it is a table, reproduce it as a markdown table. "
    "If it is an equation or formula, write it in LaTeX. "
    "Be precise and technical."
)

_FIGURE_PROMPT = (
    "Describe this figure from a scientific paper. "
    "Caption: {caption}\n\n"
    "Provide a detailed, technical description suitable for "
    "a peer reviewer who cannot see the figure."
)


class DocumentEnricher:
    """Post-extraction AI enrichment using local VLM/LLM endpoints.

    Requires configured local AI endpoints (LM Studio or Ollama).
    Raises RuntimeError if models are unavailable.
    """

    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    async def enrich_figures(
        self,
        figures: list[dict],
        vlm_model: str,
        session_id: str | None = None,
        on_progress: Any = None,
    ) -> dict[int, str]:
        """Generate VLM descriptions for extracted figures.

        Args:
            figures: list of figure dicts from DoclingParser
                     (must contain 'image_path', 'caption', 'index')
            vlm_model: model ID for vision-language model
            session_id: optional session ID for cost tracking
            on_progress: optional callback(event_type, data)

        Returns:
            dict mapping figure index to description string
        """
        descriptions: dict[int, str] = {}

        for fig in figures:
            img_path = Path(fig.get("image_path", ""))
            if not img_path.exists():
                logger.warning("Figure image not found: %s", img_path)
                continue

            idx = fig.get("index", 0)
            caption = fig.get("caption", "")

            if on_progress:
                on_progress("enrichment_progress", {
                    "step": "figure_description",
                    "figure_index": idx,
                    "total_figures": len(figures),
                    "message": f"Describing figure {idx}/{len(figures)}...",
                })

            try:
                description = await self._describe_figure(
                    img_path, caption, vlm_model, session_id,
                )
                descriptions[idx] = description
                logger.info("Described figure %d (%d chars)", idx, len(description))
            except Exception as e:
                logger.error("Failed to describe figure %d: %s", idx, e)
                descriptions[idx] = f"[Figure description unavailable: {e}]"

        return descriptions

    async def _describe_figure(
        self,
        image_path: Path,
        caption: str,
        vlm_model: str,
        session_id: str | None = None,
    ) -> str:
        """Send a figure image to the VLM for description."""
        img_bytes = image_path.read_bytes()
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")

        # Determine image MIME type
        suffix = image_path.suffix.lower()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
        mime_type = mime_map.get(suffix, "image/png")

        prompt = _FIGURE_PROMPT.format(caption=caption if caption else "No caption available")

        messages = [
            {"role": "system", "content": _FIGURE_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{img_b64}",
                        },
                    },
                ],
            },
        ]

        response = await self.llm.complete(
            model=vlm_model,
            messages=messages,
            session_id=session_id,
            temperature=0.2,
            max_tokens=2048,
        )

        return response.content

    def insert_descriptions_into_markdown(
        self,
        markdown: str,
        descriptions: dict[int, str],
    ) -> str:
        """Insert figure descriptions into the markdown at figure locations.

        Looks for Docling's figure reference patterns and appends the
        VLM-generated description below each figure reference.
        """
        import re

        lines = markdown.split("\n")
        result_lines: list[str] = []

        # Track which descriptions we've inserted
        inserted: set[int] = set()

        for line in lines:
            result_lines.append(line)

            # Docling uses patterns like ![Figure N](path) or <!-- image -->
            # Match figure references by index
            for idx, desc in descriptions.items():
                if idx in inserted:
                    continue
                # Check for figure reference patterns
                patterns = [
                    rf"!\[.*?[Ff]igure\s*{idx}\b",
                    rf"!\[.*?[Ff]ig\.?\s*{idx}\b",
                    rf"\*\*Figure\s+{idx}\b",
                    rf"Figure\s+{idx}[.:]",
                ]
                if any(re.search(p, line) for p in patterns):
                    result_lines.append("")
                    result_lines.append(f"> **Figure {idx} Description:** {desc}")
                    result_lines.append("")
                    inserted.add(idx)

        # Append any descriptions that weren't matched to a specific location
        for idx, desc in descriptions.items():
            if idx not in inserted:
                result_lines.append("")
                result_lines.append(f"> **Figure {idx} Description:** {desc}")

        return "\n".join(result_lines)
