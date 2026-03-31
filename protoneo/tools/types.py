"""Tool subsystem types and protocols."""

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ToolResult:
    """Result from a tool execution."""

    data: dict[str, Any]
    source: str
    cached: bool = False


@runtime_checkable
class Tool(Protocol):
    """An external capability available to agents."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str:
        """One-line description for agent tool-use prompts."""
        ...

    def available(self) -> bool:
        """Check if API key or service endpoint is configured."""
        ...

    async def execute(self, query: str, **kwargs: Any) -> ToolResult: ...


class ToolRegistry:
    """Registry of kernel tools.

    Agents receive the available_tools() list in their context for
    tool-use capabilities.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def available_tools(self) -> list[dict[str, str]]:
        """Return [{name, description}] for all available tools."""
        return [
            {"name": t.name, "description": t.description}
            for t in self._tools.values()
            if t.available()
        ]
