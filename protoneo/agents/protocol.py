"""
Agent protocol definition.

This is the contract that all agents must satisfy. The kernel
interacts with agents exclusively through this interface.
"""

from typing import Protocol, runtime_checkable

from .types import AgentOutput, Document, Message


class SessionContext(Protocol):
    """Read-only view of the current deliberation state."""

    @property
    def session_id(self) -> str: ...

    @property
    def messages(self) -> list[Message]: ...

    @property
    def agent_outputs(self) -> dict[str, list[AgentOutput]]: ...

    @property
    def documents(self) -> list[Document]: ...


@runtime_checkable
class AgentProtocol(Protocol):
    """
    The Agent protocol.

    Agents are stateless callables. Session state (conversation history,
    intermediate outputs) lives in the SessionContext.
    """

    @property
    def agent_id(self) -> str: ...

    @property
    def role(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def system_prompt(self) -> str: ...

    async def process(self, context: "SessionContext", message: Message) -> Message:
        """Produce a response given the current deliberation context."""
        ...

    async def review(self, document: Document) -> AgentOutput:
        """Produce structured output for a document."""
        ...
