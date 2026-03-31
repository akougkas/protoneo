"""Per-session event bus with replay for late WebSocket subscribers."""

import asyncio


class SessionEventBus:
    """Per-session event bus with replay for late WebSocket subscribers.

    Events are buffered so that a WebSocket connecting after the pipeline
    starts receives all events that were emitted before the connection opened.
    """

    def __init__(self):
        self._history: list[dict] = []
        self._subscribers: list[asyncio.Queue] = []

    def emit(self, event_type: str, data: dict) -> None:
        event = {"type": event_type, **data}
        self._history.append(event)
        for q in self._subscribers:
            q.put_nowait(event)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue[dict] = asyncio.Queue()
        for event in self._history:
            q.put_nowait(event)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    @property
    def finished(self) -> bool:
        return any(e["type"] in ("completed", "error") for e in self._history)
