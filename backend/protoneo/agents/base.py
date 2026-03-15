"""
Base agent implementation.

Concrete implementation of the AgentProtocol backed by an LLMClient.
Products subclass or configure BaseAgent to create domain-specific agents.
"""

import logging
import uuid
from typing import Any, AsyncGenerator, Callable

from ..llm.client import LLMClient
from .protocol import SessionContext
from .types import AgentOutput, Document, Message

logger = logging.getLogger("protoneo.agents.base")


class BaseAgent:
    """
    Default agent implementation.

    Builds a message sequence from the session context and system prompt,
    then calls the LLM via the shared LLMClient.
    """

    def __init__(
        self,
        role: str,
        model: str,
        system_prompt: str,
        llm_client: LLMClient,
        agent_id: str | None = None,
        focus: str = "",
        max_tokens: int = 4096,
    ):
        self._agent_id = agent_id or f"{role.lower().replace(' ', '_')}_{uuid.uuid4().hex[:6]}"
        self._role = role
        self._model = model
        self._system_prompt = system_prompt
        self._llm_client = llm_client
        self._focus = focus
        self._max_tokens = max_tokens

    @property
    def agent_id(self) -> str:
        return self._agent_id

    @property
    def role(self) -> str:
        return self._role

    @property
    def model(self) -> str:
        return self._model

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    def _build_messages(
        self,
        context: SessionContext,
        user_message: Message,
        include_history: bool = True,
    ) -> list[dict[str, str]]:
        """
        Build the OpenAI-format message list for the LLM call.

        System prompt comes first, then prior context messages (if the
        deliberation is "open" visibility), then the current user message.
        """
        msgs: list[dict[str, str]] = [{"role": "system", "content": self._system_prompt}]

        if include_history:
            for m in context.messages:
                msgs.append({"role": m.role, "content": m.content})

        msgs.append({"role": "user", "content": user_message.content})
        return msgs

    async def process(
        self,
        context: SessionContext,
        message: Message,
        session_id: str | None = None,
        include_history: bool = True,
        **kwargs: Any,
    ) -> Message:
        """
        Process a message and return the agent's response.
        """
        sid = session_id or context.session_id
        msgs = self._build_messages(context, message, include_history=include_history)

        response = await self._llm_client.complete(
            model=self._model,
            messages=msgs,
            session_id=sid,
            max_tokens=kwargs.pop("max_tokens", self._max_tokens),
            **kwargs,
        )

        return Message(
            role="assistant",
            content=response.content,
            agent_id=self._agent_id,
            metadata={
                "model": self._model,
                "usage": response.usage.model_dump(),
            },
        )

    async def process_stream(
        self,
        context: SessionContext,
        message: Message,
        session_id: str | None = None,
        include_history: bool = True,
        on_token: Callable[[str], None] | None = None,
        **kwargs: Any,
    ) -> Message:
        """Process a message with token-level streaming.

        Calls LLMClient.stream() and yields each chunk through on_token
        while accumulating the full response. Returns the complete Message
        just like process().
        """
        sid = session_id or context.session_id
        msgs = self._build_messages(context, message, include_history=include_history)

        chunks: list[str] = []
        async for chunk in self._llm_client.stream(
            model=self._model,
            messages=msgs,
            session_id=sid,
            max_tokens=kwargs.pop("max_tokens", self._max_tokens),
            **kwargs,
        ):
            chunks.append(chunk)
            if on_token:
                on_token(chunk)

        content = "".join(chunks)
        # Strip thinking tags the same way complete() does
        content = self._llm_client._strip_thinking(content)

        return Message(
            role="assistant",
            content=content,
            agent_id=self._agent_id,
            metadata={
                "model": self._model,
                "streamed": True,
            },
        )

    async def review(
        self,
        document: Document,
        session_id: str | None = None,
        extra_context: str = "",
    ) -> AgentOutput:
        """
        Review a document and produce structured output.

        Sends the full document text (or concatenated chunks) as the user
        message, with the agent's system prompt providing review instructions.
        """
        doc_content = document.text if document.text else "\n\n".join(document.chunks)
        if extra_context:
            doc_content = f"{extra_context}\n\n{doc_content}"

        msgs: list[dict[str, str]] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": doc_content},
        ]

        response = await self._llm_client.complete(
            model=self._model,
            messages=msgs,
            session_id=session_id,
        )

        return AgentOutput(
            agent_id=self._agent_id,
            agent_role=self._role,
            content=response.content,
            metadata={
                "model": self._model,
                "document_id": document.document_id,
                "usage": response.usage.model_dump(),
            },
        )

    def __repr__(self) -> str:
        return f"BaseAgent(id={self._agent_id!r}, role={self._role!r}, model={self._model!r})"
