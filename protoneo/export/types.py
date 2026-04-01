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
    """Registry of export formats.

    Exporters can be registered globally (app_name="") or scoped to a
    specific application. When resolving by format name, app-scoped
    exporters take precedence over global ones.
    """

    def __init__(self):
        self._exporters: dict[str, Exporter] = {}
        # App-scoped exporters: (app_name, format_name) -> Exporter
        self._app_exporters: dict[tuple[str, str], Exporter] = {}

    def register(self, exporter: Exporter, app_name: str = "") -> None:
        if app_name:
            self._app_exporters[(app_name, exporter.format_name)] = exporter
        else:
            self._exporters[exporter.format_name] = exporter

    def get(self, format_name: str, app_name: str = "") -> Exporter | None:
        if app_name:
            scoped = self._app_exporters.get((app_name, format_name))
            if scoped:
                return scoped
        return self._exporters.get(format_name)

    def available_formats(self, app_name: str = "") -> list[dict[str, str]]:
        """Return [{format_name, mime_type, file_extension}]."""
        seen: set[str] = set()
        result = []
        # App-scoped exporters first
        if app_name:
            for (aname, _), e in self._app_exporters.items():
                if aname == app_name:
                    seen.add(e.format_name)
                    result.append({
                        "format_name": e.format_name,
                        "mime_type": e.mime_type,
                        "file_extension": e.file_extension,
                    })
        # Global exporters (unless overridden by app-scoped)
        for e in self._exporters.values():
            if e.format_name not in seen:
                result.append({
                    "format_name": e.format_name,
                    "mime_type": e.mime_type,
                    "file_extension": e.file_extension,
                })
        return result
