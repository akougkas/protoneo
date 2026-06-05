"""External tools for agents (graph query, Semantic Scholar, Web Search)."""

from .types import Tool, ToolResult, ToolRegistry
from .graph_query import GraphQueryTool, graph_fact_digest, query_graph
from .semantic_scholar import SemanticScholarTool
from .web_search import WebSearchTool

__all__ = [
    "Tool",
    "ToolResult",
    "ToolRegistry",
    "GraphQueryTool",
    "SemanticScholarTool",
    "WebSearchTool",
    "create_tool_registry",
    "graph_fact_digest",
    "query_graph",
]


def create_tool_registry(graph: object | None = None) -> ToolRegistry:
    """Create a ToolRegistry with all built-in tools registered.

    When a knowledge graph is supplied, the local ``query_graph`` tool is bound
    to it so reviewers and the PC Chair can run deterministic graph queries.
    """
    registry = ToolRegistry()
    registry.register(GraphQueryTool(graph))  # type: ignore[arg-type]
    registry.register(SemanticScholarTool())
    registry.register(WebSearchTool())
    return registry
