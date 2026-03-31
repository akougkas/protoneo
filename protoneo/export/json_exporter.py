"""Built-in JSON exporter: raw session result as JSON."""

import json
from typing import Any


class JsonExporter:
    """Exports raw session data as JSON."""

    @property
    def format_name(self) -> str:
        return "json"

    @property
    def mime_type(self) -> str:
        return "application/json"

    @property
    def file_extension(self) -> str:
        return ".json"

    async def export(self, session: Any, app_data: dict | None = None) -> bytes:
        data = {
            "session_id": session.session_id,
            "status": session.status.value if hasattr(session.status, "value") else str(session.status),
            "created_at": str(session.created_at),
            "result": session.result,
            "config": session.config,
        }
        if app_data:
            data["app_data"] = app_data
        return json.dumps(data, indent=2, default=str).encode("utf-8")
