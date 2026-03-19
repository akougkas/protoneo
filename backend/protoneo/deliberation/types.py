"""
Deliberation type definitions.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..agents.types import AgentOutput, Message


class DeliberationRules(BaseModel):
    """Controls for a deliberation session."""

    max_rounds: int = 3
    timeout_seconds: int = 600
    visibility: str = Field(default="open", description="'open' (agents see each other) or 'blind'")


class PhaseResult(BaseModel):
    """Output from a single deliberation phase."""

    phase_name: str
    mode: str
    outputs: list[AgentOutput] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    failed_agents: list[dict[str, Any]] = Field(default_factory=list)
    duration_seconds: float = 0.0


class DeliberationResult(BaseModel):
    """Complete result of a deliberation session."""

    session_id: str
    phases: list[PhaseResult] = Field(default_factory=list)
    final_output: AgentOutput | None = None
    total_cost: float = 0.0
    duration_seconds: float = 0.0
    completed_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
