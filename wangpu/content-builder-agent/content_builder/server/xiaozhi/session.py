"""Session, MCP, and device-tool state for Xiaozhi hardware."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import struct
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, WebSocket

from content_builder.config import DEFAULT_THREAD_ID
from content_builder.history import save_thread_history_snapshot
from content_builder.server.events import app_events
from content_builder.server.security import pairing_token

from .constants import (
    ALLOWED_DEVICE_TOOLS,
    OPUS_FRAME_DURATION_MS,
    XIAOZHI_PHOTO_UPLOAD_WAIT_TIMEOUT,
    _is_photo_like_device_tool,
    _resolve_device_tool_name,
)
from .logging import _print_structured_panel
from .vision import _save_vision_data_uri_sync

logger = logging.getLogger(__name__)


def _photo_payload_has_file(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    file_payload = payload.get("file")
    return isinstance(file_payload, dict) and bool(file_payload.get("path"))


def _photo_payload_has_data_uri(payload: Any) -> bool:
    return isinstance(payload, dict) and str(payload.get("image_data_uri") or "").startswith("data:image/")


def _structured_log_value(value: Any, *, indent: int = 2, max_text: int = 800) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{prefix}(empty)"]
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(_structured_log_value(item, indent=indent + 2, max_text=max_text))
            else:
                lines.append(f"{prefix}{key}: {_structured_log_scalar(item, max_text=max_text)}")
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}(empty)"]
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(_structured_log_value(item, indent=indent + 2, max_text=max_text))
            else:
                lines.append(f"{prefix}- {_structured_log_scalar(item, max_text=max_text)}")
        return lines
    return [f"{prefix}{_structured_log_scalar(value, max_text=max_text)}"]


def _structured_log_scalar(value: Any, *, max_text: int = 800) -> str:
    text = str(value)
    return text if len(text) <= max_text else f"{text[:max_text]}... <truncated {len(text) - max_text} chars>"


def _print_structured_panel(title: str, fields: dict[str, Any]) -> None:
    print(f"\n=== {title} ===", flush=True)
    for line in _structured_log_value(fields, indent=2):
        print(line, flush=True)


def _extract_text_content_blocks(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if isinstance(result, dict):
        content = result.get("content")
    else:
        content = payload.get("content")
    if not isinstance(content, list):
        return []
    return [
        str(item.get("text"))
        for item in content
        if isinstance(item, dict) and item.get("text") is not None
    ]


def _save_photo_payload_from_data_uri(
    payload: dict[str, Any],
    *,
    thread_id: str,
    question: str,
) -> tuple[dict[str, Any], bool]:
    payload_question = str(payload.get("question") or question or "")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
    upload_payload, _image_path = _save_vision_data_uri_sync(
        thread_id,
        str(payload.get("image_data_uri") or ""),
        payload_question,
        metadata=metadata,
    )
    return upload_payload, True


def _extract_photo_upload_payload(
    payload: Any,
    *,
    thread_id: str,
    question: str = "",
) -> tuple[dict[str, Any], bool] | None:
    if _photo_payload_has_file(payload):
        return payload, False
    if _photo_payload_has_data_uri(payload):
        return _save_photo_payload_from_data_uri(payload, thread_id=thread_id, question=question)
    if isinstance(payload, dict):
        result = payload.get("result")
        if _photo_payload_has_file(result):
            return result, False
        if _photo_payload_has_data_uri(result):
            return _save_photo_payload_from_data_uri(result, thread_id=thread_id, question=question)
    for text in _extract_text_content_blocks(payload):
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            parsed = json.loads(text)
            if _photo_payload_has_file(parsed):
                return parsed, False
            if _photo_payload_has_data_uri(parsed):
                return _save_photo_payload_from_data_uri(parsed, thread_id=thread_id, question=question)
            if isinstance(parsed, dict) and _photo_payload_has_file(parsed.get("result")):
                return parsed["result"], False
            if isinstance(parsed, dict) and _photo_payload_has_data_uri(parsed.get("result")):
                return _save_photo_payload_from_data_uri(parsed["result"], thread_id=thread_id, question=question)
    return None

def _agent_thread_id(thread_id: str) -> str:
    candidate = (thread_id or DEFAULT_THREAD_ID).strip()
    try:
        return str(uuid.UUID(candidate))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"content-builder-agent:xiaozhi:{candidate}"))

@dataclass
class XiaozhiSession:
    websocket: WebSocket
    thread_id: str
    device_id: str
    client_id: str
    version: int = 1
    session_id: str = field(default_factory=lambda: f"xz-{uuid.uuid4().hex}")
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: dict[int, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    next_request_id: int = 1
    audio_frames: list[bytes] = field(default_factory=list)
    manual_listen_started: bool = False
    current_listen_mode: str = "auto"
    current_user_transcript: str = ""
    tts_sample_rate: int = 24000
    server_is_speaking: bool = False
    auto_vad_codec: OpusCodec | None = None
    auto_speech_started: bool = False
    auto_speech_started_at: float = 0.0
    auto_last_voice_at: float = 0.0
    auto_speech_frames: list[bytes] = field(default_factory=list)
    photo_uploads: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    vision_url: str = ""
    last_photo_upload_at: float = 0.0
    agent_task: asyncio.Task[None] | None = None
    turn_started_at: float = 0.0
    turn_log: Any = None
    closed: bool = False

    async def send_json(self, payload: dict[str, Any]) -> None:
        payload.setdefault("session_id", self.session_id)
        await self.websocket.send_text(json.dumps(payload, ensure_ascii=False))

    async def send_binary(self, payload: bytes) -> None:
        if self.version == 2:
            packet = struct.pack("!HHIII", 2, 0, 0, 0, len(payload)) + payload
        elif self.version == 3:
            packet = struct.pack("!BBH", 0, 0, len(payload)) + payload
        else:
            packet = payload
        await self.websocket.send_bytes(packet)

    def unpack_audio(self, payload: bytes) -> bytes:
        if self.version == 2:
            if len(payload) < 16:
                return b""
            _version, packet_type, _reserved, _timestamp, payload_size = struct.unpack("!HHIII", payload[:16])
            if packet_type != 0:
                return b""
            return payload[16 : 16 + payload_size]
        if self.version == 3:
            if len(payload) < 4:
                return b""
            packet_type, _reserved, payload_size = struct.unpack("!BBH", payload[:4])
            if packet_type != 0:
                return b""
            return payload[4 : 4 + payload_size]
        return payload

    async def send_mcp_request(self, method: str, params: dict[str, Any] | None = None, *, timeout: int = 20) -> dict[str, Any]:
        request_id = self.next_request_id
        self.next_request_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self.pending[request_id] = future
        params = params or {}
        if method == "tools/call":
            _print_structured_panel(
                "Xiaozhi MCP Tool Call",
                {
                    "thread_id": self.thread_id,
                    "session_id": self.session_id,
                    "request_id": request_id,
                    "tool": params.get("name", ""),
                    "arguments": params.get("arguments", {}),
                    "transport": "websocket:mcp",
                },
            )
        await self.send_json(
            {
                "type": "mcp",
                "payload": {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                    "id": request_id,
                },
            }
        )
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            if method == "tools/call":
                _print_structured_panel(
                    "Xiaozhi MCP Tool Result",
                    {
                        "thread_id": self.thread_id,
                        "session_id": self.session_id,
                        "request_id": request_id,
                        "tool": params.get("name", ""),
                        "result": response.get("result", response) if isinstance(response, dict) else response,
                    },
                )
            return response
        finally:
            self.pending.pop(request_id, None)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None, *, timeout: int = 20) -> dict[str, Any]:
        resolved_name = _resolve_device_tool_name(name, self.tools)
        if resolved_name not in ALLOWED_DEVICE_TOOLS:
            raise HTTPException(status_code=403, detail=f"Device tool is not allowed: {name}")
        if self.tools and resolved_name not in self.tools:
            exposed = ", ".join(sorted(self.tools)) or "none"
            raise HTTPException(
                status_code=404,
                detail=f"Connected device does not expose tool: {resolved_name}; exposed tools: {exposed}",
            )
        if resolved_name != name:
            logger.info("Resolved Xiaozhi device tool alias %s -> %s", name, resolved_name)
        return await self.send_mcp_request("tools/call", {"name": resolved_name, "arguments": arguments or {}}, timeout=timeout)

    async def call_photo_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        self.clear_photo_uploads()
        question = str((arguments or {}).get("question") or self.current_user_transcript or "")
        logger.info(
            "Calling Xiaozhi photo tool thread=%s tool=%s vision_url=%s",
            self.thread_id,
            name,
            self.vision_url or "not-initialized",
        )
        tool_task = asyncio.create_task(self.call_tool(name, arguments, timeout=XIAOZHI_PHOTO_UPLOAD_WAIT_TIMEOUT))
        upload_task = asyncio.create_task(self.wait_for_photo_upload())
        try:
            done, _pending = await asyncio.wait(
                {tool_task, upload_task},
                return_when=asyncio.FIRST_COMPLETED,
                timeout=XIAOZHI_PHOTO_UPLOAD_WAIT_TIMEOUT,
            )
            if upload_task in done:
                if not tool_task.done():
                    tool_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await tool_task
                return upload_task.result()
            if tool_task in done:
                try:
                    tool_result = tool_task.result()
                except TimeoutError as exc:
                    with contextlib.suppress(TimeoutError):
                        return await asyncio.wait_for(upload_task, timeout=XIAOZHI_PHOTO_UPLOAD_WAIT_TIMEOUT)
                    raise HTTPException(
                        status_code=504,
                        detail=(
                            "Xiaozhi photo tool timed out before an image was uploaded; "
                            f"vision_url={self.vision_url or 'not-initialized'}"
                        ),
                    ) from exc
                extracted = _extract_photo_upload_payload(tool_result, thread_id=self.thread_id, question=question)
                if extracted is not None:
                    photo_payload, saved_from_mcp_result = extracted
                    if saved_from_mcp_result:
                        await self.publish_photo_upload(photo_payload)
                    return photo_payload
                with contextlib.suppress(TimeoutError):
                    return await asyncio.wait_for(upload_task, timeout=XIAOZHI_PHOTO_UPLOAD_WAIT_TIMEOUT)
                logger.warning(
                    "Xiaozhi photo tool returned before upload thread=%s tool=%s result=%s vision_url=%s",
                    self.thread_id,
                    name,
                    tool_result,
                    self.vision_url or "not-initialized",
                )

            logger.warning(
                "Xiaozhi photo upload timed out thread=%s tool=%s vision_url=%s tools=%s",
                self.thread_id,
                name,
                self.vision_url or "not-initialized",
                sorted(self.tools),
            )
            raise HTTPException(
                status_code=504,
                detail=(
                    "Xiaozhi photo upload timed out; "
                    f"vision_url={self.vision_url or 'not-initialized'}"
                ),
            )
        finally:
            for task in (tool_task, upload_task):
                if not task.done():
                    task.cancel()

    async def remember_photo_upload(self, payload: dict[str, Any]) -> None:
        self.last_photo_upload_at = time.monotonic()
        await self.photo_uploads.put(payload)

    async def publish_photo_upload(self, payload: dict[str, Any]) -> None:
        self.last_photo_upload_at = time.monotonic()
        file_payload = payload.get("file") if isinstance(payload.get("file"), dict) else {}
        await asyncio.to_thread(
            save_thread_history_snapshot,
            str(payload.get("thread_id") or self.thread_id),
            event={
                "type": "xiaozhi_photo_upload",
                "question": str(payload.get("question") or ""),
                "file": file_payload,
            },
        )
        await session_manager.publish(str(payload.get("thread_id") or self.thread_id), "photo_uploaded", payload)

    async def wait_for_photo_upload(self) -> dict[str, Any]:
        return await asyncio.wait_for(self.photo_uploads.get(), timeout=XIAOZHI_PHOTO_UPLOAD_WAIT_TIMEOUT)

    def clear_photo_uploads(self) -> None:
        while True:
            try:
                self.photo_uploads.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def initialize_mcp(self, base: str) -> None:
        vision_token = pairing_token()
        self.vision_url = (
            f"{base}/mcp/vision/explain"
            f"?thread_id={quote(self.thread_id)}&token={quote(vision_token)}"
        )
        logger.info("Xiaozhi MCP vision URL thread=%s url=%s", self.thread_id, self.vision_url)
        params = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {"listChanged": True},
                "sampling": {},
                "vision": {
                    "url": self.vision_url,
                    "token": vision_token,
                }
            },
            "clientInfo": {
                "name": "XiaozhiClient",
                "version": "1.0.0",
            },
        }
        with contextlib.suppress(Exception):
            await self.send_mcp_request("initialize", params)
        with contextlib.suppress(Exception):
            cursor = ""
            seen_cursors: set[str] = set()
            for _ in range(20):
                result = await self.send_mcp_request("tools/list", {"cursor": cursor})
                self.remember_tools(result)
                result_payload = result.get("result") if isinstance(result, dict) else None
                next_cursor = ""
                if isinstance(result_payload, dict):
                    next_cursor = str(result_payload.get("nextCursor") or result_payload.get("next_cursor") or "")
                _print_structured_panel(
                    "Xiaozhi MCP Tools Page",
                    {
                        "thread_id": self.thread_id,
                        "session_id": self.session_id,
                        "cursor": cursor or "<first>",
                        "next_cursor": next_cursor or "<end>",
                        "tools": _summarize_tool_payload(result_payload),
                    },
                )
                if not next_cursor or next_cursor in seen_cursors:
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor
        _print_structured_panel(
            "Xiaozhi MCP Tools Ready",
            {
                "thread_id": self.thread_id,
                "session_id": self.session_id,
                "tool_count": len(self.tools),
                "allowed_tools": [name for name in sorted(self.tools) if name in ALLOWED_DEVICE_TOOLS],
                "blocked_or_unknown_tools": [name for name in sorted(self.tools) if name not in ALLOWED_DEVICE_TOOLS],
                "camera_tools": [
                    name
                    for name in sorted(self.tools)
                    if name in {"take_photo", "self.camera.take_photo", "take_screenshot"}
                ],
            },
        )

    def remember_tools(self, payload: dict[str, Any]) -> None:
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            return
        tools = result.get("tools")
        if not isinstance(tools, list):
            return
        for tool in tools:
            if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                self.tools[str(tool["name"])] = tool

    async def handle_mcp_payload(self, payload: dict[str, Any]) -> None:
        request_id = payload.get("id")
        if isinstance(request_id, int) and request_id in self.pending:
            future = self.pending[request_id]
            if not future.done():
                future.set_result(payload)
            return
        await self.publish("mcp", {"thread_id": self.thread_id, "session_id": self.session_id, "payload": payload})

    async def send_tts_sentence_start(self, text: str = "") -> None:
        await self.send_json({"type": "tts", "state": "sentence_start", "text": text})

    async def send_tts_sentence_end(self) -> None:
        await self.send_json({"type": "tts", "state": "sentence_end"})

    async def send_llm_emotion(self, emotion: str, text: str = "") -> None:
        await self.send_json({"type": "llm", "emotion": emotion, "text": text, "content": text})

    async def send_tool_event(self, event: dict[str, Any]) -> None:
        text = _format_tool_event_text(event)
        await self.send_tts_sentence_start(text)
        await self.send_tts_sentence_end()

    async def publish(self, event: str, payload: dict[str, Any]) -> None:
        await session_manager.publish(self.thread_id, event, payload)

    def close_pending(self, reason: str) -> None:
        self.closed = True
        for future in list(self.pending.values()):
            if not future.done():
                future.set_exception(RuntimeError(reason))
        self.pending.clear()
        if self.agent_task and not self.agent_task.done():
            self.agent_task.cancel()

    def reset_auto_vad(self) -> None:
        self.auto_speech_started = False
        self.auto_speech_started_at = 0.0
        self.auto_last_voice_at = 0.0
        self.auto_speech_frames.clear()


class XiaozhiSessionManager:
    def __init__(self) -> None:
        self.by_session: dict[str, XiaozhiSession] = {}
        self.by_thread: dict[str, XiaozhiSession] = {}
        self.listeners: dict[str, set[asyncio.Queue[tuple[str, dict[str, Any]]]]] = defaultdict(set)

    def register(self, session: XiaozhiSession) -> None:
        self.by_session[session.session_id] = session
        self.by_thread[session.thread_id] = session
        self.by_thread[_agent_thread_id(session.thread_id)] = session

    def unregister(self, session: XiaozhiSession) -> None:
        self.by_session.pop(session.session_id, None)
        for key in {session.thread_id, _agent_thread_id(session.thread_id)}:
            if self.by_thread.get(key) is session:
                self.by_thread.pop(key, None)
        session.close_pending("Xiaozhi device disconnected")

    def active_for_thread(self, thread_id: str) -> XiaozhiSession | None:
        return self.by_thread.get(thread_id) or (next(iter(self.by_session.values()), None) if thread_id == "current" else None)

    async def publish(self, thread_id: str, event: str, payload: dict[str, Any]) -> None:
        for key in {thread_id, "*"}:
            queues = list(self.listeners.get(key, set()))
            for queue in queues:
                await queue.put((event, payload))
        await app_events.publish(f"xiaozhi_{event}", payload, thread_id=thread_id)
        if event == "connected":
            await app_events.publish("thread_created", payload, thread_id=thread_id)
        elif event == "message":
            role = "ai" if payload.get("role") == "assistant" else "human" if payload.get("role") == "human" else str(payload.get("role") or "")
            content = str(payload.get("content") or "")
            if role and content:
                await app_events.publish(
                    "message_appended",
                    {
                        "source": "xiaozhi",
                        "message": {
                            "id": f"xiaozhi-{thread_id}-{role}-{int(time.time() * 1000)}",
                            "type": role,
                            "content": content,
                        },
                    },
                    thread_id=thread_id,
                )
                await app_events.publish("thread_updated", {"source": "xiaozhi"}, thread_id=thread_id)
        elif event in {"photo", "photo_uploaded"}:
            await app_events.publish("artifact_updated", payload, thread_id=thread_id)

    @contextlib.asynccontextmanager
    async def subscribe(self, thread_id: str):
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self.listeners[thread_id].add(queue)
        try:
            yield queue
        finally:
            self.listeners[thread_id].discard(queue)


session_manager = XiaozhiSessionManager()


def _format_tool_event_text(event: dict[str, Any]) -> str:
    name = str(event.get("name") or "tool")
    return f"正在执行工具：\n{name}"


def _summarize_tool_payload(result_payload: Any) -> list[dict[str, Any]]:
    if not isinstance(result_payload, dict):
        return []
    tools = result_payload.get("tools")
    if not isinstance(tools, list):
        return []
    summaries: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "")
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        schema_keys: list[str] = []
        required: list[str] = []
        if isinstance(schema, dict):
            properties = schema.get("properties")
            if isinstance(properties, dict):
                schema_keys = sorted(str(key) for key in properties)
            raw_required = schema.get("required")
            if isinstance(raw_required, list):
                required = [str(item) for item in raw_required]
        summaries.append(
            {
                "name": name,
                "allowed": name in ALLOWED_DEVICE_TOOLS,
                "description": str(tool.get("description") or "")[:240],
                "schema_keys": schema_keys,
                "required": required,
            }
        )
    return summaries
