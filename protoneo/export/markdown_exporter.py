"""Built-in Markdown exporter: generic deliberation results as readable Markdown."""

from typing import Any


class GenericMarkdownExporter:
    """Exports deliberation results as a generic Markdown document."""

    @property
    def format_name(self) -> str:
        return "markdown"

    @property
    def mime_type(self) -> str:
        return "text/markdown"

    @property
    def file_extension(self) -> str:
        return ".md"

    async def export(self, session: Any, app_data: dict | None = None) -> bytes:
        parts: list[str] = []
        parts.append(f"# Deliberation Results: {session.session_id}\n")
        parts.append(f"**Status:** {session.status}\n")
        parts.append(f"**Created:** {session.created_at}\n")

        result = session.result
        if result and isinstance(result, dict):
            phases = result.get("phases", [])
            for phase in phases:
                phase_name = phase.get("phase_name", "Unknown Phase")
                parts.append(f"\n## {phase_name}\n")
                for output in phase.get("outputs", []):
                    role = output.get("agent_role", "Agent")
                    content = output.get("content", "")
                    parts.append(f"### {role}\n")
                    parts.append(f"{content}\n")

        return "\n".join(parts).encode("utf-8")
