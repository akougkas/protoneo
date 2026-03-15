"""
Agent-related type definitions.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Message(BaseModel):
    """A single message in a deliberation context."""

    role: str = Field(description="'system', 'user', or 'assistant'")
    content: str
    agent_id: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentOutput(BaseModel):
    """Structured output produced by an agent."""

    agent_id: str
    agent_role: str
    content: str
    structured: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class Document(BaseModel):
    """An ingested document available to agents."""

    document_id: str
    filename: str
    text: str
    chunks: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GroundingSource(BaseModel):
    """A source agents can use for grounded responses."""

    source_type: str = Field(description="'document', 'retrieval', 'tool'")
    source_id: str
    config: dict[str, Any] = Field(default_factory=dict)
