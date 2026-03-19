"""
Session management.

A session is the unit of work in ProtoNeo. It holds documents, agent
assignments, deliberation config, and accumulated results. Sessions
are stored as JSON on disk (SQLite is a future upgrade path).
"""

import json
import logging
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..agents.types import AgentOutput, Document, Message

logger = logging.getLogger("protoneo.deliberation.session")


class SessionStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class StepState(BaseModel):
    """State of a single pipeline step."""

    status: str = "pending"  # pending, running, complete, failed
    started_at: float | None = None
    completed_at: float | None = None
    model_used: str = ""
    error: str = ""
    nodes_added: int = 0
    edges_added: int = 0
    entities_flagged: int = 0


class SessionContext:
    """
    Mutable session state. The deliberation engine reads and writes
    through this object. Agents receive a read-only view.
    """

    def __init__(self, session_id: str):
        self._session_id = session_id
        self._messages: list[Message] = []
        self._agent_outputs: dict[str, list[AgentOutput]] = {}
        self._documents: list[Document] = []
        self._metadata: dict[str, Any] = {}

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def messages(self) -> list[Message]:
        return self._messages

    @property
    def agent_outputs(self) -> dict[str, list[AgentOutput]]:
        return self._agent_outputs

    @property
    def documents(self) -> list[Document]:
        return self._documents

    @property
    def metadata(self) -> dict[str, Any]:
        return self._metadata

    def add_message(self, message: Message) -> None:
        self._messages.append(message)

    def add_output(self, output: AgentOutput) -> None:
        self._agent_outputs.setdefault(output.agent_id, []).append(output)

    def add_document(self, document: Document) -> None:
        self._documents.append(document)


class Session(BaseModel):
    """Persistent session record."""

    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    status: SessionStatus = SessionStatus.CREATED
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    agent_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None

    # Unified paper graph (persisted, survives restart)
    paper_text: str = ""
    paper_markdown: str = ""
    paper_graph: dict[str, Any] | None = None

    # Pipeline stage tracking
    current_stage: str = ""

    # Step-level pipeline tracking (keys: nlp_prepass, ontology, extract, coref, verify, summarize)
    pipeline_steps: dict[str, Any] = Field(default_factory=dict)
    # Graph snapshots after each pipeline step
    graph_after_step: dict[str, Any] = Field(default_factory=dict)

    # Batch membership
    batch_id: str = ""
    # Graph provenance: "extracted" (built by pipeline) or "imported" (uploaded JSON)
    graph_source: str = "extracted"


class Batch(BaseModel):
    """A batch of sessions for overnight graph building."""

    batch_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    session_ids: list[str] = Field(default_factory=list)
    conference: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = "running"  # running, completed, partial, failed


class BatchManager:
    """Creates, persists, and retrieves batches.

    Storage is JSON-on-disk, mirroring SessionManager.
    """

    def __init__(self, storage_dir: str | Path):
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    def _batch_path(self, batch_id: str) -> Path:
        return self._storage_dir / f"{batch_id}.json"

    async def create(self, conference: str = "", session_ids: list[str] | None = None) -> Batch:
        batch = Batch(conference=conference, session_ids=session_ids or [])
        self._save(batch)
        logger.info("Created batch %s with %d sessions", batch.batch_id, len(batch.session_ids))
        return batch

    async def get(self, batch_id: str) -> Batch | None:
        path = self._batch_path(batch_id)
        if not path.exists():
            return None
        return Batch.model_validate_json(path.read_text())

    async def update(self, batch: Batch) -> None:
        self._save(batch)

    async def list_batches(self, limit: int = 50) -> list[Batch]:
        batches: list[Batch] = []
        for p in sorted(self._storage_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                batches.append(Batch.model_validate_json(p.read_text()))
            except Exception:
                continue
            if len(batches) >= limit:
                break
        return batches

    def _save(self, batch: Batch) -> None:
        self._batch_path(batch.batch_id).write_text(
            batch.model_dump_json(indent=2)
        )


class SessionManager:
    """
    Creates, persists, and retrieves sessions.

    Storage is JSON-on-disk for now. The interface is async-ready so
    swapping in SQLite or Postgres later is non-breaking.
    """

    def __init__(self, storage_dir: str | Path):
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._contexts: dict[str, SessionContext] = {}

    def _session_path(self, session_id: str) -> Path:
        return self._storage_dir / f"{session_id}.json"

    async def create(self, config: dict[str, Any] | None = None) -> Session:
        session = Session(config=config or {})
        self._save(session)
        self._contexts[session.session_id] = SessionContext(session.session_id)
        logger.info("Created session %s", session.session_id)
        return session

    async def get(self, session_id: str) -> Session | None:
        path = self._session_path(session_id)
        if not path.exists():
            return None
        return Session.model_validate_json(path.read_text())

    async def update(self, session: Session) -> None:
        session.updated_at = datetime.utcnow()
        self._save(session)

    async def list_sessions(self, limit: int = 50) -> list[Session]:
        sessions: list[Session] = []
        for p in sorted(self._storage_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                sessions.append(Session.model_validate_json(p.read_text()))
            except Exception:
                continue
            if len(sessions) >= limit:
                break
        return sessions

    def get_context(self, session_id: str) -> SessionContext:
        if session_id not in self._contexts:
            self._contexts[session_id] = SessionContext(session_id)
        return self._contexts[session_id]

    def _save(self, session: Session) -> None:
        self._session_path(session.session_id).write_text(
            session.model_dump_json(indent=2)
        )
