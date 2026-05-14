"""流式事件解析与终端打印。

LangGraph 的 stream 会同时产生多种事件：messages 是模型 token/chunk，
updates 是节点完成后的状态更新，tasks 是任务开始/结束。这个模块把它们规整成
统一的 StreamEvent，供 CLI 打印，也供外部 Python API 消费。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

from .multimodal import build_user_content


MAX_LOG_TEXT = 500
StreamEventType = Literal[
    "token",
    "task",
    "tool_call",
    "tool_result",
    "approval",
    "node_update",
    "final",
    "error",
]


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


def parse_stream_chunk(chunk: Any) -> tuple[str | None, Any, Any]:
    """把不同版本 LangGraph 的 stream chunk 规整成 type/ns/data。

    新版 v2 事件固定是 dict；旧版在多 stream_mode 或 subgraphs=True 时可能是 tuple。
    保留兼容逻辑可以让这个 demo 在不同本地依赖版本中更稳。
    """

    if isinstance(chunk, dict):
        return chunk.get("type"), chunk.get("ns", ()), chunk.get("data")

    if isinstance(chunk, tuple) and len(chunk) == 3:
        namespace, mode, payload = chunk
        return mode, namespace, payload

    if isinstance(chunk, tuple) and len(chunk) == 2:
        first, payload = chunk
        known_modes = {"updates", "messages", "tasks", "values", "debug", "custom"}
        if isinstance(first, str) and first in known_modes:
            return first, (), payload

        namespace = first
        if isinstance(payload, dict):
            return payload.get("type"), namespace, payload.get("data", payload)
        return None, namespace, payload

    return None, (), chunk


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
        from langchain.messages import RemoveMessage
    except Exception:
        try:
            from langchain_core.messages import RemoveMessage
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

    messages 提供 token 级输出；updates/tasks 提供过程状态。三者同时开启后，
    CLI 能像 Codex 一样逐段显示模型正文，又能清楚看到工具和子 Agent 的进展。
    images 可选传入图片 URL、data URL 或本地路径；这些图片不会走单独工具，而是
    和文本一起组成 user message 的多模态 content，直接交给 Deep Agents 背后的
    ChatQwen 多模态模型。
    """

    removed_messages = trim_checkpoint_messages(
        agent,
        thread_id=thread_id,
        max_turns=max_turns,
    )
    if removed_messages:
        yield StreamEvent(
            "node_update",
            "main",
            f"[memory] 已按配置保留最近 {max_turns} 轮，清理 {removed_messages} 条旧消息",
        )

    streamed_tokens: list[str] = []
    final_answer = ""
    user_content = build_user_content(message, images)

    for chunk in agent.stream(
        {"messages": [{"role": "user", "content": user_content}]},
        config={"configurable": {"thread_id": thread_id}},
        stream_mode=["messages", "updates", "tasks"],
        subgraphs=True,
        version="v2",
    ):
        chunk_type, namespace, data = parse_stream_chunk(chunk)
        source = source_from_namespace(namespace)

        if chunk_type == "messages":
            if not isinstance(data, tuple) or len(data) != 2:
                continue
            message_chunk, metadata = data
            token = text_from_content(getattr(message_chunk, "content", ""))
            if token:
                streamed_tokens.append(token)
                yield StreamEvent("token", source, token, {"chunk": message_chunk, "metadata": metadata})

        elif chunk_type == "updates":
            update_final = final_text_from_update(data)
            if update_final:
                final_answer = update_final
            yield from _events_from_update(source, data)

        elif chunk_type == "tasks":
            yield _event_from_task(source, data)

    final_text = final_answer or "".join(streamed_tokens).strip()
    if final_text:
        yield StreamEvent("final", "main", final_text)


def print_stream_event(event: StreamEvent) -> None:
    """把结构化流事件打印到控制台。

    token 用 end="" 连续输出；其他事件前后留空行，避免和正文粘在一起。
    final 事件主要给 chat_once/API 使用；CLI 已经实时打印过 token，因此不再重复输出。
    """

    if event.type == "token":
        print(event.text, end="", flush=True)
        return

    if event.type == "final":
        return

    if event.text:
        print(f"\n{event.text}", flush=True)
