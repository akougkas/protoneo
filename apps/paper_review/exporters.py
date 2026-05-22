"""Exporter protocol wrappers for Paper Review export formats.

Wraps the existing packet_to_markdown and packet_to_pdf functions
in the kernel Exporter protocol for registration into ExportRegistry.
"""

from typing import Any

from .export import packet_to_markdown, packet_to_pdf
from .review import session_to_review_packet


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
        packet = session_to_review_packet(session)
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
        packet = session_to_review_packet(session)
        return packet_to_pdf(packet)
