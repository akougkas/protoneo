"""Exporter protocol wrappers for Paper Review export formats.

Wraps the existing packet_to_markdown and packet_to_pdf functions
in the kernel Exporter protocol for registration into ExportRegistry.
"""

from typing import Any

from protoneo.deliberation.types import DeliberationResult
from protoneo.knowledge.graph import KnowledgeGraph

from .conference import ConferenceProfile, load_profile
from .export import packet_to_markdown, packet_to_pdf
from .review import result_to_packet


def _session_to_packet(session: Any):
    """Build a ReviewPacket from a completed session's stored data."""
    if not session.result:
        raise ValueError("Session has no result to export")

    result = DeliberationResult.model_validate(session.result)

    conference_slug = session.config.get("metadata", {}).get("conference", "hpdc26")
    try:
        profile = load_profile(conference_slug)
    except FileNotFoundError:
        profile = ConferenceProfile(slug=conference_slug, name=conference_slug)

    paper_title = session.config.get("metadata", {}).get("paper_title", "")
    final_review = session.result.get("final_review", {})

    packet = result_to_packet(result, profile, paper_title, final_review=final_review)

    if session.knowledge_graph:
        try:
            pg = KnowledgeGraph.model_validate(session.knowledge_graph)
            packet.graph_summary = pg.summary
            packet.graph_node_count = len(pg.nodes)
            packet.graph_edge_count = len(pg.edges)
        except Exception:
            pass

    return packet


class ReviewMarkdownExporter:
    """Renders a ReviewPacket as formatted Markdown."""

    @property
    def format_name(self) -> str:
        return "review-markdown"

    @property
    def mime_type(self) -> str:
        return "text/markdown"

    @property
    def file_extension(self) -> str:
        return ".md"

    async def export(self, session: Any, app_data: dict | None = None) -> bytes:
        packet = _session_to_packet(session)
        md = packet_to_markdown(packet)
        return md.encode("utf-8")


class ReviewPdfExporter:
    """Renders a ReviewPacket as PDF via WeasyPrint."""

    @property
    def format_name(self) -> str:
        return "review-pdf"

    @property
    def mime_type(self) -> str:
        return "application/pdf"

    @property
    def file_extension(self) -> str:
        return ".pdf"

    async def export(self, session: Any, app_data: dict | None = None) -> bytes:
        packet = _session_to_packet(session)
        return packet_to_pdf(packet)
