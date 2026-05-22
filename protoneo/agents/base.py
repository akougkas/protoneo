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
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        min_p: float | None = None,
        repeat_penalty: float | None = None,
        reasoning_effort: str | None = None,
        presence_penalty: float | None = None,
        frequency_penalty: float | None = None,
    ):
        self._agent_id = agent_id or f"{role.lower().replace(' ', '_')}_{uuid.uuid4().hex[:6]}"
        self._role = role
        self._model = model
        self._system_prompt = system_prompt
        self._llm_client = llm_client
        self._focus = focus
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._top_p = top_p
        self._top_k = top_k
        self._min_p = min_p
        self._repeat_penalty = repeat_penalty
        self._reasoning_effort = reasoning_effort
        self._presence_penalty = presence_penalty
        self._frequency_penalty = frequency_penalty

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

    def _inference_kwargs(self) -> dict[str, Any]:
        """Build per-agent inference overrides for LLM calls."""
        kw: dict[str, Any] = {}
        if self._temperature is not None:
            kw["temperature"] = self._temperature
        if self._top_p is not None:
            kw["top_p"] = self._top_p
        local_extra: dict[str, Any] = {}
        if self._top_k is not None:
            local_extra["top_k"] = self._top_k
        if self._min_p is not None:
            local_extra["min_p"] = self._min_p
        if self._repeat_penalty is not None:
            local_extra["repeat_penalty"] = self._repeat_penalty
        if local_extra:
            # OpenAI-compatible local servers such as llama-server and LM Studio
            # accept sampler controls in the raw request body.
            kw["extra_body"] = local_extra
        if self._reasoning_effort is not None:
            kw["reasoning_effort"] = self._reasoning_effort
        if self._presence_penalty is not None:
            kw["presence_penalty"] = self._presence_penalty
        if self._frequency_penalty is not None:
            kw["frequency_penalty"] = self._frequency_penalty
        return kw

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

        Enforces strict user/assistant alternation required by some model
        templates (e.g., Qwen on LM Studio). Consecutive same-role messages
        are merged into one.
        """
        msgs: list[dict[str, str]] = [{"role": "system", "content": self._system_prompt}]

        if include_history and context.messages:
            for m in context.messages:
                role = m.role if m.role in ("user", "assistant") else "assistant"
                # Qwen jinja templates require the first non-system message to be
                # a user message. If history starts with assistant, fold it into
                # the upcoming user message instead of inserting it directly.
                if len(msgs) == 1 and role == "assistant":
                    # Will be prepended to user message below
                    continue
                if msgs and msgs[-1]["role"] == role:
                    msgs[-1]["content"] += "\n\n" + m.content
                else:
                    msgs.append({"role": role, "content": m.content})

            # Ensure history ends with assistant so the next user message
            # creates proper alternation
            if msgs[-1]["role"] == "user":
                last_user = msgs.pop()
                user_message_content = last_user["content"] + "\n\n" + user_message.content
                msgs.append({"role": "user", "content": user_message_content})
                return msgs

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

        # Merge per-agent inference params; caller kwargs take precedence
        call_kwargs = {**self._inference_kwargs(), **kwargs}

        response = await self._llm_client.complete(
            model=self._model,
            messages=msgs,
            session_id=sid,
            max_tokens=call_kwargs.pop("max_tokens", self._max_tokens),
            **call_kwargs,
        )

        return Message(
            role="assistant",
            content=response.content,
            agent_id=self._agent_id,
            metadata={
                "model": self._model,
                "usage": response.usage.model_dump(),
                "temperature": call_kwargs.get("temperature", self._temperature),
                "top_p": call_kwargs.get("top_p", self._top_p),
                "top_k": self._top_k,
                "min_p": self._min_p,
                "repeat_penalty": self._repeat_penalty,
                "reasoning_effort": self._reasoning_effort,
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

        # Merge per-agent inference params; caller kwargs take precedence
        call_kwargs = {**self._inference_kwargs(), **kwargs}

        chunks: list[str] = []
        async for chunk in self._llm_client.stream(
            model=self._model,
            messages=msgs,
            session_id=sid,
            max_tokens=call_kwargs.pop("max_tokens", self._max_tokens),
            **call_kwargs,
        ):
            chunks.append(chunk)
            if on_token:
                on_token(chunk)

        content = "".join(chunks)
        # Strip thinking tags the same way complete() does
        content = self._llm_client._strip_thinking(content)

        # Capture usage from the final streaming chunk when available
        stream_usage = getattr(self._llm_client, "_last_stream_usage", {})

        return Message(
            role="assistant",
            content=content,
            agent_id=self._agent_id,
            metadata={
                "model": self._model,
                "streamed": True,
                "temperature": call_kwargs.get("temperature", self._temperature),
                "top_p": call_kwargs.get("top_p", self._top_p),
                "top_k": self._top_k,
                "min_p": self._min_p,
                "repeat_penalty": self._repeat_penalty,
                "reasoning_effort": self._reasoning_effort,
                "usage": stream_usage if stream_usage else {},
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
