"""Project-local Qwen model adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from uuid import uuid4
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_qwq import ChatQwen


def _tool_call_prefix() -> str:
    return f"call_qwen_{uuid4().hex}"


def _ensure_tool_call_ids_on_message(message: Any, prefix: str) -> None:
    """Fill missing tool-call IDs produced by Qwen-compatible endpoints.

    Deep Agents' built-in ``task`` tool must return a ``ToolMessage`` bound to
    the parent model tool-call ID. Some DashScope-compatible responses expose
    valid tool names and arguments but leave that ID empty, which makes
    subagent invocation fail before the subagent can start.
    """

    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        for index, tool_call in enumerate(tool_calls):
            if isinstance(tool_call, dict) and not tool_call.get("id"):
                tool_call["id"] = f"{prefix}_{index}"

    tool_call_chunks = getattr(message, "tool_call_chunks", None)
    if tool_call_chunks:
        for index, tool_call in enumerate(tool_call_chunks):
            if not isinstance(tool_call, dict) or tool_call.get("id"):
                continue
            chunk_index = tool_call.get("index", index)
            tool_call["id"] = f"{prefix}_{chunk_index}"


def _ensure_tool_call_ids_on_result(result: ChatResult) -> ChatResult:
    prefix = _tool_call_prefix()
    for generation_index, generation in enumerate(result.generations):
        _ensure_tool_call_ids_on_message(
            getattr(generation, "message", None),
            f"{prefix}_{generation_index}",
        )
    return result


class StableToolCallChatQwen(ChatQwen):
    """ChatQwen variant that guarantees non-empty LangChain tool-call IDs."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        return _ensure_tool_call_ids_on_result(result)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        result = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
        return _ensure_tool_call_ids_on_result(result)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        prefix = _tool_call_prefix()
        for chunk in super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
            _ensure_tool_call_ids_on_message(chunk.message, prefix)
            yield chunk

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        prefix = _tool_call_prefix()
        async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            _ensure_tool_call_ids_on_message(chunk.message, prefix)
            yield chunk
