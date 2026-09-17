"""Bounded session event replay and live delivery to WebSocket subscribers."""

import asyncio
from collections import deque


class SessionEventBus:
    def __init__(self, history_limit: int = 2048, subscriber_limit: int = 2048):
        self._history: deque[dict] = deque(maxlen=history_limit)
        self._subscribers: list[asyncio.Queue] = []
        self._subscriber_limit = max(1, subscriber_limit)
        self._streams: dict[str, deque[str]] = {}
        self._finished = False

    @staticmethod
    def _enqueue(queue: asyncio.Queue, event: dict) -> None:
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(event)

    def reset(self) -> None:
        self._history.clear()
        self._streams.clear()
        self._finished = False
        for queue in self._subscribers:
            while not queue.empty():
                queue.get_nowait()
        self.emit("session_reset", {})

    def emit(self, event_type: str, data: dict) -> None:
        if event_type == "prompt_rendered":
            return  # Prompt snapshots are persisted by the application, not UI events.
        event = {**data, "type": event_type}
        agent_id = str(data.get("agent_id", ""))
        if event_type == "token":
            # Store only a bounded in-progress tail, not every token for all time.
            self._streams.setdefault(agent_id, deque(maxlen=512)).append(str(data.get("chunk", ""))[:4096])
        elif event_type != "prompt_rendered":
            if event_type in {"agent_start", "agent_done", "agent_error"}:
                self._streams.pop(agent_id, None)
            self._history.append(event)
        if event_type in {"completed", "error", "pipeline_cancelled", "batch_complete"}:
            self._finished = True
            self._streams.clear()
        for queue in self._subscribers:
            self._enqueue(queue, event)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=self._subscriber_limit)
        for event in self._history:
            self._enqueue(queue, event)
        for agent_id, chunks in self._streams.items():
            self._enqueue(queue, {"type": "token", "agent_id": agent_id, "chunk": "".join(chunks)})
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    @property
    def finished(self) -> bool:
        return self._finished
