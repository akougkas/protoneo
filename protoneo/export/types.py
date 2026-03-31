"""Export subsystem types and protocols."""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Exporter(Protocol):
    """Renders session results into a specific output format."""

    @property
    def format_name(self) -> str:
        """Lookup key: 'json', 'markdown', 'pdf', 'latex'."""
        ...

    @property
    def mime_type(self) -> str: ...

    @property
    def file_extension(self) -> str: ...

    async def export(self, session: Any, app_data: dict | None = None) -> bytes:
        """Render session results. app_data comes from session.app_data."""
        ...


class ExportRegistry:
    """Registry of export formats."""

    def __init__(self):
        self._exporters: dict[str, Exporter] = {}

    def register(self, exporter: Exporter) -> None:
        self._exporters[exporter.format_name] = exporter

    def get(self, format_name: str) -> Exporter | None:
        return self._exporters.get(format_name)

    def available_formats(self) -> list[dict[str, str]]:
        """Return [{format_name, mime_type, file_extension}]."""
        return [
            {
                "format_name": e.format_name,
                "mime_type": e.mime_type,
                "file_extension": e.file_extension,
            }
            for e in self._exporters.values()
        ]
