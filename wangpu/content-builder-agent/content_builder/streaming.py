"""流式事件解析与终端打印。

CLI 只使用 LangChain/LangGraph 的 stream_events(version="v3") 协议。
v3 的 messages、tools、custom、updates、values 和 lifecycle projection 会在这里
规整成统一的 StreamEvent，供终端打印，也供外部 Python API 消费。
"""

from __future__ import annotations

import json
import os
import re
import sys
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

from .multimodal import build_user_content

try:
    from langchain_core._api import LangChainBetaWarning

    warnings.filterwarnings(
        "ignore",
        message=r"The v3 streaming protocol on Pregel is experimental.*",
        category=LangChainBetaWarning,
    )
except Exception:
    warnings.filterwarnings(
        "ignore",
        message=r"The v3 streaming protocol on Pregel is experimental.*",
    )

MAX_LOG_TEXT = 500
SHOW_THINKING_ENV = "CONTENT_BUILDER_SHOW_THINKING"
SHOW_DEBUG_EVENTS_ENV = "CONTENT_BUILDER_SHOW_DEBUG_EVENTS"
SHOW_SUBAGENT_TOKENS_ENV = "CONTENT_BUILDER_SHOW_SUBAGENT_TOKENS"
StreamEventType = Literal[
    "token",
    "thinking",
    "task",
    "tool_call",
    "tool_result",
    "sandbox_output",
    "approval",
    "node_update",
    "final",
    "error",
]


def configure_console_encoding() -> None:
    """Keep Windows console output from crashing on emoji or non-GBK text."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


@dataclass
class StreamEvent:
    """对外暴露的结构化流事件。

    type 用来区分 token、工具、任务和最终结果；source 标记事件来自主 Agent
    还是子图；text 是适合直接展示的文本；raw 保留原始对象，方便未来接 UI。
    """

    type: StreamEventType
    source: str
    text: str
    raw: Any = None


def text_from_content(content: Any) -> str:
    """把不同模型返回的 message content 统一转成纯文本。

    有些模型返回字符串，有些返回 content block 列表。统一处理后，streaming
    和 chat 层就不需要关心具体模型供应商的格式差异。
    """

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


def short_text(content: Any, limit: int = 300) -> str:
    """生成工具结果的短预览，避免长文档或 write_file 内容刷屏。"""

    text = text_from_content(content).replace("\n", " ").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def reasoning_from_message(message: Any) -> str:
    """Extract provider-specific reasoning/thinking text from a message chunk."""

    additional_kwargs = getattr(message, "additional_kwargs", None)
    if isinstance(additional_kwargs, dict):
        reasoning = additional_kwargs.get("reasoning_content") or additional_kwargs.get("reasoning")
        if reasoning:
            return str(reasoning)

    response_metadata = getattr(message, "response_metadata", None)
    if isinstance(response_metadata, dict):
        reasoning = response_metadata.get("reasoning_content") or response_metadata.get("reasoning")
        if reasoning:
            return str(reasoning)

    return ""


def compact_for_log(value: Any, limit: int = MAX_LOG_TEXT) -> Any:
    """递归压缩日志对象。

    工具参数里可能包含整篇文章或很长的图片提示词。打印完整内容会淹没 token 流，
    所以这里只压缩日志展示，不改变真实传给工具的数据。
    """

    if isinstance(value, str):
        return value if len(value) <= limit else f"{value[:limit]}..."
    if isinstance(value, dict):
        return {key: compact_for_log(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [compact_for_log(item, limit) for item in value]
    return value


def format_args(args: Any) -> str:
    """把工具参数格式化为易读 JSON，并保留中文字符。"""

    return json.dumps(compact_for_log(args), ensure_ascii=False, indent=2)


def source_from_namespace(namespace: Any) -> str:
    """根据 LangGraph namespace 判断事件来源。

    subgraphs=True 时，子 Agent 或子图事件会带 namespace。为了控制台可读，这里把
    很长的 task id 截短，只保留足够定位的一小段。
    """

    if not namespace:
        return "main"
    for segment in namespace:
        if isinstance(segment, str) and ":" in segment:
            node_name, task_id = segment.split(":", 1)
            return f"{node_name}:{task_id[:8]}"
    return "/".join(str(segment) for segment in namespace)


def extract_messages(value: Any) -> list[Any]:
    """递归提取 update payload 中的 messages。

    Deep Agents 的不同节点会把 message 放在不同层级。这里递归提取后，updates
    处理逻辑只需要关注消息本身。
    """

    if isinstance(value, dict):
        messages = []
        raw_messages = value.get("messages", [])
        if isinstance(raw_messages, list):
            messages.extend(raw_messages)
        for child_value in value.values():
            if child_value is not raw_messages:
                messages.extend(extract_messages(child_value))
        return messages

    if isinstance(value, list):
        messages = []
        for item in value:
            messages.extend(extract_messages(item))
        return messages

    return []


def _event_for_tool_call(source: str, tool_call: dict[str, Any]) -> StreamEvent:
    """把 AIMessage.tool_calls 转成可读事件。"""

    name = tool_call.get("name", "unknown_tool")
    args = tool_call.get("args", {})

    if name == "write_todos":
        lines = [f"[{source}] 创建/更新 todo"]
        for item in args.get("todos", []):
            status = item.get("status", "pending")
            content = item.get("content", "")
            lines.append(f"  - [{status}] {content}")
        return StreamEvent("tool_call", source, "\n".join(lines), tool_call)

    if name == "task":
        return StreamEvent(
            "tool_call",
            source,
            f"[{source}] 委派子智能体\n{format_args(args)}",
            tool_call,
        )

    if name == "execute":
        command = args.get("command", "") if isinstance(args, dict) else ""
        return StreamEvent(
            "approval",
            source,
            (
                f"[{source}] 本地命令等待确认\n"
                f"  command: {command}\n"
                "  note: execute 会在本地 shell 中运行，必须经用户确认后才会执行。"
            ),
            tool_call,
        )

    if name in {"write_file", "edit_file"}:
        return StreamEvent(
            "tool_call",
            source,
            f"[{source}] 文件变更请求: {name}\n{format_args(args)}",
            tool_call,
        )

    return StreamEvent(
        "tool_call",
        source,
        f"[{source}] 调用工具: {name}\n{format_args(args)}",
        tool_call,
    )


def _event_for_tool_result(source: str, message: Any) -> StreamEvent:
    """把 ToolMessage 转成结构化工具结果事件。"""

    tool_name = getattr(message, "name", "tool")
    content = getattr(message, "content", "")
    preview = short_text(content)

    if tool_name == "execute":
        lowered = text_from_content(content).lower()
        status = "unknown"
        if "command succeeded" in lowered:
            status = "succeeded"
        elif "command failed" in lowered:
            status = "failed"
        elif "command not executed" in lowered:
            status = "not_executed"

        return StreamEvent(
            "tool_result",
            source,
            f"[{source}] 命令执行结果: {status}\n{preview}",
            message,
        )

    return StreamEvent(
        "tool_result",
        source,
        f"[{source}] 工具返回: {tool_name}\n{preview}",
        message,
    )


def _events_from_update(source: str, data: Any) -> Iterator[StreamEvent]:
    """处理 updates 事件。

    注意：updates 是节点完成后的完整状态，不适合拿来打印正文，否则会和 messages
    token 流重复。因此这里只输出节点摘要、工具调用和工具返回。
    """

    if not isinstance(data, dict):
        return

    for node_name, node_data in data.items():
        if str(node_name).startswith("__"):
            continue

        yield StreamEvent(
            "node_update",
            source,
            f"[{source}] 节点更新: {node_name}",
            node_data,
        )

        for message in extract_messages(node_data):
            for tool_call in getattr(message, "tool_calls", []) or []:
                yield _event_for_tool_call(source, tool_call)

            if getattr(message, "type", "") == "tool":
                yield _event_for_tool_result(source, message)


def final_text_from_update(data: Any) -> str:
    """从 updates 事件中提取最新完整 AI 正文。

    messages 事件用于实时展示 token；最终返回值则更适合从节点完成后的完整
    AIMessage 中拿。这样 chat_once 不会把子 Agent 中间 token 全部拼进最终答案。
    """

    final_text = ""
    if not isinstance(data, dict):
        return final_text

    for node_data in data.values():
        for message in extract_messages(node_data):
            if getattr(message, "type", "") != "ai":
                continue
            if getattr(message, "tool_calls", []) or []:
                continue
            text = text_from_content(getattr(message, "content", "")).strip()
            if text:
                final_text = text
    return final_text


def final_text_from_state(data: Any) -> str:
    """Extract the latest assistant text from a full state snapshot."""

    final_text = ""
    for message in extract_messages(data):
        if getattr(message, "type", "") != "ai":
            continue
        if getattr(message, "tool_calls", []) or []:
            continue
        text = text_from_content(getattr(message, "content", "")).strip()
        if text:
            final_text = text
    return final_text


def _event_from_task(source: str, data: Any) -> StreamEvent:
    """处理 tasks 事件，输出任务开始、结束和错误信息。"""

    if not isinstance(data, dict):
        return StreamEvent("task", source, f"[{source}] task 事件", data)

    task_name = data.get("name", "unknown_task")
    task_id = str(data.get("id", ""))[:8]

    if "input" in data:
        return StreamEvent("task", source, f"[task:start] {source} -> {task_name} ({task_id})", data)

    text = f"[task:done] {source} -> {task_name} ({task_id})"
    if data.get("error"):
        text += f"\n  错误: {data['error']}"
        return StreamEvent("error", source, text, data)
    return StreamEvent("task", source, text, data)


def _event_from_custom(source: str, data: Any) -> StreamEvent | None:
    """Convert custom LangGraph events into displayable stream events."""

    if not isinstance(data, dict) or data.get("type") != "sandbox_output":
        return None
    chunk = str(data.get("chunk") or "")
    if not chunk and data.get("event") not in {"start", "end", "error"}:
        return None
    command = str(data.get("command") or "execute")
    if data.get("event") == "start":
        text = f"[{source}] 沙盒命令开始: {command}"
    elif chunk:
        text = chunk
    else:
        text = f"[{source}] 沙盒命令结束: {command}"
    return StreamEvent("sandbox_output", source, text, data)


def _split_v3_payload(value: Any) -> tuple[Any, Any]:
    """Normalize v3 channel payloads.

    Raw v3 protocol events commonly carry message data as ``(payload, metadata)``
    tuples, while tests and some projections may hand us the payload directly or
    wrapped in a single-item list.
    """

    if isinstance(value, list) and value:
        value = value[0]
    if isinstance(value, tuple) and len(value) == 2:
        return value[0], value[1]
    return value, None


def _first_payload(value: Any) -> Any:
    return _split_v3_payload(value)[0]


def _source_from_v3_namespace(namespace: Any) -> str:
    if not namespace:
        return "main"
    return source_from_namespace(namespace)


def _events_from_v3_message(source: str, data: Any) -> Iterator[StreamEvent]:
    payload, metadata = _split_v3_payload(data)
    if not isinstance(payload, dict):
        return

    event_name = str(payload.get("event", ""))
    if event_name == "message-start":
        node = "model"
        if isinstance(metadata, dict):
            node = str(metadata.get("langgraph_node") or metadata.get("lc_agent_name") or node)
        yield StreamEvent("node_update", source, f"[{source}] 节点更新: {node}", {"payload": payload, "metadata": metadata})

    elif event_name == "content-block-delta":
        delta = payload.get("delta") or {}
        delta_type = delta.get("type") if isinstance(delta, dict) else ""
        if delta_type == "text-delta":
            text = str(delta.get("text") or "")
            if text:
                yield StreamEvent("token", source, text, payload)
        elif delta_type == "reasoning-delta":
            reasoning = str(delta.get("reasoning") or delta.get("text") or "")
            if reasoning:
                yield StreamEvent("thinking", source, reasoning, payload)
        elif delta_type in {"tool-call-delta", "tool_call_delta"}:
            tool_name = str(delta.get("name") or delta.get("tool_name") or "tool")
            args = delta.get("args") or delta.get("input") or ""
            preview = short_text(args, limit=180)
            if preview:
                yield StreamEvent(
                    "tool_call",
                    source,
                    f"[{source}] 工具参数流: {tool_name}\n{preview}",
                    payload,
                )

    elif event_name == "message-finish":
        message = payload.get("message") or payload.get("output")
        reasoning = reasoning_from_message(message) if message is not None else ""
        if reasoning:
            yield StreamEvent("thinking", source, reasoning, payload)


def _events_from_v3_tool(source: str, data: Any) -> Iterator[StreamEvent]:
    payload = _first_payload(data)
    if not isinstance(payload, dict):
        return

    event_name = str(payload.get("event", ""))
    tool_name = str(payload.get("tool_name") or payload.get("name") or "tool")
    if event_name == "tool-started":
        tool_input = payload.get("input", payload.get("args", {}))
        yield StreamEvent(
            "tool_call",
            source,
            f"[{source}] 调用工具: {tool_name}\n{format_args(tool_input)}",
            payload,
        )
    elif event_name == "tool-output-delta":
        delta = payload.get("delta", payload.get("chunk", ""))
        text = text_from_content(delta)
        if text:
            yield StreamEvent("sandbox_output", source, text, payload)
    elif event_name == "tool-finished":
        output = payload.get("output", payload.get("result", ""))
        yield StreamEvent(
            "tool_result",
            source,
            f"[{source}] 工具返回: {tool_name}\n{short_text(output)}",
            payload,
        )
    elif event_name == "tool-error":
        error = payload.get("error", "unknown tool error")
        yield StreamEvent(
            "error",
            source,
            f"[{source}] 工具错误: {tool_name}\n{error}",
            payload,
        )


def _event_from_v3_lifecycle(source: str, data: Any) -> StreamEvent | None:
    payload = _first_payload(data)
    if not isinstance(payload, dict):
        return None

    event_name = str(payload.get("event", ""))
    graph_name = str(payload.get("graph_name") or source)
    if event_name in {"failed", "interrupted"}:
        error = payload.get("error") or payload.get("cause") or ""
        return StreamEvent("error", source, f"[{source}] {graph_name} {event_name}\n{error}", payload)
    if event_name in {"started", "running", "completed"}:
        return StreamEvent("task", source, f"[{source}] {graph_name}: {event_name}", payload)
    return None


def _message_role(message: Any) -> str:
    """兼容不同消息对象的角色字段。

    LangChain 消息通常有 type，例如 human/ai/tool；部分地方也可能出现 role。
    统一成字符串后，裁剪逻辑就不依赖具体消息类。
    """

    return str(getattr(message, "type", getattr(message, "role", "")))


def _messages_to_remove_for_turn_limit(messages: list[Any], max_turns: int) -> list[Any]:
    """计算超过轮次上限时应该删除哪些消息。

    “一轮”按一条 human/user 消息开始，到下一条 human/user 消息之前结束。这样删除时
    会整轮删除，避免留下孤立的 tool message 或缺少 tool result 的 AI tool_call。
    """

    if max_turns <= 0:
        return []

    turn_starts = [
        index
        for index, message in enumerate(messages)
        if _message_role(message) in {"human", "user"}
    ]
    if len(turn_starts) <= max_turns:
        return []

    first_kept_turn = turn_starts[-max_turns]
    return [message for message in messages[:first_kept_turn] if getattr(message, "id", None)]


def trim_checkpoint_messages(agent: Any, *, thread_id: str, max_turns: int | None) -> int:
    """按配置裁剪 checkpoint 中的历史消息。

    MemorySaver 本身没有“保留最近 N 轮”的构造参数；它只保存 checkpoint。
    因此这里在每轮运行前读取当前 state，并用 RemoveMessage 写入删除操作。

    返回：
        实际删除的消息数量。返回 0 表示无需裁剪或无法裁剪。
    """

    if max_turns is None or max_turns <= 0:
        return 0

    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = agent.get_state(config)
        values = getattr(snapshot, "values", {}) or {}
        messages = list(values.get("messages", []) or [])
    except Exception:
        # 旧依赖或某些 runnable 可能没有 get_state；裁剪失败不应阻断正常对话。
        return 0

    stale_messages = _messages_to_remove_for_turn_limit(messages, max_turns)
    if not stale_messages:
        return 0

    try:
        from langchain_core.messages import RemoveMessage
    except Exception:
        try:
            from langchain.messages import RemoveMessage
        except Exception:
            return 0

    try:
        agent.update_state(
            config,
            {"messages": [RemoveMessage(id=message.id) for message in stale_messages]},
        )
    except Exception:
        return 0

    return len(stale_messages)


def stream_agent_events(
    agent: Any,
    message: str,
    *,
    thread_id: str,
    max_turns: int | None = None,
    images: Iterable[str | Path] | None = None,
) -> Iterator[StreamEvent]:
    """运行 Agent 并产出结构化流事件。

    v3 messages 提供 token 级输出；updates/tasks/lifecycle 提供过程状态。
    CLI 能像 Codex 一样逐段显示模型正文，又能清楚看到工具和子 Agent 的进展。
    images 可选传入图片 URL、data URL 或本地路径；这些图片不会走单独工具，而是
    和文本一起组成 user message 的多模态 content，直接交给 Deep Agents 背后的
    ChatQwen 多模态模型。
    """

    removed_messages = trim_checkpoint_messages(agent, thread_id=thread_id, max_turns=max_turns)
    if removed_messages:
        yield StreamEvent(
            "node_update",
            "main",
            f"[memory] 已按配置保留最近 {max_turns} 轮，清理 {removed_messages} 条旧消息",
        )

    yield from _stream_agent_events_v3(agent, message, thread_id=thread_id, images=images)


def _stream_agent_events_v3(
    agent: Any,
    message: str,
    *,
    thread_id: str,
    images: Iterable[str | Path] | None = None,
) -> Iterator[StreamEvent]:
    """Stream with LangChain/LangGraph event streaming v3."""

    stream_events = getattr(agent, "stream_events", None)
    if not callable(stream_events):
        raise RuntimeError(
            'This agent requires LangChain/LangGraph stream_events(version="v3"). '
            "Update dependencies with `uv sync` or use the project `uv run` environment."
        )

    user_content = build_user_content(message, images)
    try:
        stream = stream_events(
            {"messages": [{"role": "user", "content": user_content}]},
            config={"configurable": {"thread_id": thread_id}},
            version="v3",
        )
    except (TypeError, ValueError, NotImplementedError) as exc:
        raise RuntimeError(
            'This agent requires LangChain/LangGraph stream_events(version="v3"). '
            "The installed runnable does not support v3 event streaming."
        ) from exc

    main_tokens: list[str] = []
    final_answer = ""

    for raw_event in stream:
        if not isinstance(raw_event, dict):
            continue

        method = str(raw_event.get("method", ""))
        params = raw_event.get("params") or {}
        if not isinstance(params, dict):
            continue

        source = _source_from_v3_namespace(params.get("namespace") or ())
        data = params.get("data")

        if method == "messages":
            for event in _events_from_v3_message(source, data):
                if event.type == "token" and source == "main":
                    main_tokens.append(event.text)
                yield event

        elif method == "tools":
            yield from _events_from_v3_tool(source, data)

        elif method == "custom":
            event = _event_from_custom(source, data)
            if event:
                yield event

        elif method.startswith("custom:"):
            yield StreamEvent("sandbox_output", source, text_from_content(data), data)

        elif method == "values":
            text = final_text_from_state(data)
            if source == "main" and text:
                final_answer = text

        elif method == "updates":
            text = final_text_from_update(data)
            if source == "main" and text:
                final_answer = text
            yield from _events_from_update(source, data)

        elif method == "lifecycle":
            event = _event_from_v3_lifecycle(source, data)
            if event:
                yield event

        elif method == "tasks":
            yield _event_from_task(source, _first_payload(data))

    final_text = final_answer or "".join(main_tokens).strip()
    if final_text:
        yield StreamEvent("final", "main", final_text)

def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _supports_color() -> bool:
    return bool(getattr(sys.stdout, "isatty", lambda: False)()) and not os.environ.get("NO_COLOR")


def _strip_source_prefix(text: str, source: str) -> str:
    prefix = f"[{source}] "
    if text.startswith(prefix):
        return text[len(prefix) :]
    return text


def _summarize_event_text(text: str, *, source: str) -> str:
    text = _strip_source_prefix(text.strip(), source)
    return re.sub(r"\n{3,}", "\n\n", text)


class ConsoleStreamPrinter:
    """Pretty terminal renderer for StreamEvent objects.

    The stream parser intentionally exposes every LangGraph/Deep Agents event.
    The terminal, however, should privilege the assistant answer and show process
    details only when they are useful. This renderer keeps that display policy in
    one stateful place so token text, thinking text, and tool progress do not run
    into each other.
    """

    def __init__(
        self,
        *,
        show_thinking: bool | None = None,
        show_debug_events: bool | None = None,
        show_subagent_tokens: bool | None = None,
        use_color: bool | None = None,
    ) -> None:
        configure_console_encoding()
        self.show_thinking = (
            _env_flag(SHOW_THINKING_ENV, default=True) if show_thinking is None else show_thinking
        )
        self.show_debug_events = (
            _env_flag(SHOW_DEBUG_EVENTS_ENV, default=True) if show_debug_events is None else show_debug_events
        )
        self.show_subagent_tokens = (
            _env_flag(SHOW_SUBAGENT_TOKENS_ENV) if show_subagent_tokens is None else show_subagent_tokens
        )
        self.use_color = _supports_color() if use_color is None else use_color
        self._answer_started = False
        self._answer_open = False
        self._thinking_started = False
        self._sandbox_started = False

    def print(self, event: StreamEvent) -> None:
        if event.type == "token":
            self._print_token(event)
            return

        if event.type == "thinking":
            self._print_thinking(event)
            return

        if event.type == "final":
            if not self._answer_started and event.text:
                self._print_token(StreamEvent("token", "main", event.text, event.raw))
            self.finish()
            return

        if event.type == "sandbox_output":
            self._print_sandbox(event)
            return

        if event.type in {"task", "node_update"} and not self.show_debug_events:
            return

        self._print_event_panel(event)

    def finish(self) -> None:
        if self._answer_open:
            print(flush=True)
            self._answer_open = False

    def _style(self, text: str, code: str) -> str:
        if not self.use_color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def _print_token(self, event: StreamEvent) -> None:
        if event.source != "main" and not self.show_subagent_tokens:
            return

        if not self._answer_started:
            label = "助手> " if event.source == "main" else f"{event.source}> "
            print(f"\n{self._style(label, '1;32')}", end="", flush=True)
            self._answer_started = True
        elif not self._answer_open:
            print(self._style("继续> ", "1;32"), end="", flush=True)

        print(event.text, end="", flush=True)
        self._answer_open = True

    def _print_thinking(self, event: StreamEvent) -> None:
        if not self.show_thinking:
            return

        self._close_answer_line()
        if not self._thinking_started:
            print(f"\n{self._style('思考>', '2;36')} ", end="", flush=True)
            self._thinking_started = True
        print(self._style(event.text, "2"), end="", flush=True)

    def _print_sandbox(self, event: StreamEvent) -> None:
        self._close_answer_line()
        if not self._sandbox_started:
            print(f"\n{self._style('命令输出>', '1;35')}", flush=True)
            self._sandbox_started = True
        print(event.text, end="", flush=True)

    def _print_event_panel(self, event: StreamEvent) -> None:
        self._close_answer_line()

        body = _summarize_event_text(event.text, source=event.source)
        if not body:
            return

        title_by_type = {
            "approval": "等待确认",
            "error": "运行错误",
            "node_update": "节点",
            "task": "任务",
            "tool_call": "工具调用",
            "tool_result": "工具结果",
        }
        title = title_by_type.get(event.type, event.type)
        if event.source != "main":
            title = f"{title} ({event.source})"

        print(f"\n{self._style('> ' + title, '1;34')}", flush=True)
        for line in body.splitlines():
            print(f"  {line}", flush=True)

    def _close_answer_line(self) -> None:
        if self._answer_open:
            print(flush=True)
            self._answer_open = False


_DEFAULT_CONSOLE_PRINTER = ConsoleStreamPrinter()


def print_stream_event(event: StreamEvent) -> None:
    """把结构化流事件打印到控制台。

    这个函数保留给旧调用方使用。新的 CLI 路径会为每一轮对话创建
    ConsoleStreamPrinter 实例，从而得到更稳定的换行和分区效果。
    """

    _DEFAULT_CONSOLE_PRINTER.print(event)
