"""External tools for agents (Semantic Scholar, Web Search)."""

from .types import Tool, ToolResult, ToolRegistry
from .semantic_scholar import SemanticScholarTool
from .web_search import WebSearchTool

__all__ = [
    "Tool",
    "ToolResult",
    "ToolRegistry",
    "SemanticScholarTool",
    "WebSearchTool",
    "create_tool_registry",
]


def create_tool_registry() -> ToolRegistry:
    """Create a ToolRegistry with all built-in tools registered."""
    registry = ToolRegistry()
    registry.register(SemanticScholarTool())
    registry.register(WebSearchTool())
    return registry
