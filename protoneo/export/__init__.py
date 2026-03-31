"""Export subsystem: format registry and built-in exporters."""

from .types import Exporter, ExportRegistry
from .json_exporter import JsonExporter
from .markdown_exporter import GenericMarkdownExporter

__all__ = [
    "Exporter",
    "ExportRegistry",
    "JsonExporter",
    "GenericMarkdownExporter",
    "create_export_registry",
]


def create_export_registry() -> ExportRegistry:
    """Create an ExportRegistry with all built-in exporters registered."""
    registry = ExportRegistry()
    registry.register(JsonExporter())
    registry.register(GenericMarkdownExporter())
    return registry
