"""Xiaozhi ESP32 hardware gateway.

This module speaks the Xiaozhi WebSocket/MCP protocol on the LAN side and
adapts device capabilities into Content Builder conversations.
"""

from __future__ import annotations

import asyncio
import audioop
import contextlib
import ipaddress
import json
import logging
import os
import re
import socket
import struct
import subprocess
import tempfile
import time
import uuid
import wave
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from langgraph_sdk import get_client
from fastapi.responses import JSONResponse, Response, StreamingResponse
from langchain_core.messages import HumanMessage

from content_builder.config import DEFAULT_THREAD_ID, load_main_config, create_qwen_model
from content_builder.history import save_thread_history_snapshot
from content_builder.multimodal import build_user_content
from content_builder.server.security import extract_request_token, pairing_token, token_is_valid
from content_builder.streaming import final_text_from_state, final_text_from_update, source_from_namespace, text_from_content
from content_builder.thread_storage import resolve_thread_file, thread_paths
from content_builder.voice.console import MainTokenDeltaFilter, UserEchoFilter, ensure_voice_ready
from content_builder.voice.tts import VoiceResponseSpeaker


router = APIRouter(prefix="/api/xiaozhi", tags=["xiaozhi"])
compat_router = APIRouter(tags=["xiaozhi"])
logger = logging.getLogger(__name__)

XIAOZHI_DEFAULT_THREAD_ID = os.environ.get("CONTENT_BUILDER_XIAOZHI_THREAD_ID", "xiaozhi-hardware")
OPUS_FRAME_DURATION_MS = 60
OPUS_INPUT_SAMPLE_RATE = 16000
OPUS_OUTPUT_SAMPLE_RATE = 16000
OPUS_CHANNELS = 1
OPUS_SAMPLE_WIDTH = 2
XIAOZHI_TTS_PREBUFFER_FRAMES = 3
XIAOZHI_TTS_TAIL_DRAIN_MS = int(os.environ.get("CONTENT_BUILDER_XIAOZHI_TTS_TAIL_DRAIN_MS", "120"))
XIAOZHI_AUTO_VAD_RMS_THRESHOLD = int(os.environ.get("CONTENT_BUILDER_XIAOZHI_VAD_RMS_THRESHOLD", "500"))
XIAOZHI_AUTO_VAD_MIN_SPEECH_MS = int(os.environ.get("CONTENT_BUILDER_XIAOZHI_VAD_MIN_SPEECH_MS", "300"))
XIAOZHI_AUTO_VAD_SILENCE_MS = int(os.environ.get("CONTENT_BUILDER_XIAOZHI_VAD_SILENCE_MS", "800"))
XIAOZHI_AUTO_VAD_MAX_SPEECH_MS = int(os.environ.get("CONTENT_BUILDER_XIAOZHI_VAD_MAX_SPEECH_MS", "12000"))
XIAOZHI_PHOTO_UPLOAD_WAIT_TIMEOUT = int(os.environ.get("CONTENT_BUILDER_XIAOZHI_PHOTO_UPLOAD_WAIT_TIMEOUT", "60"))
MAX_IMAGE_BYTES = 20 * 1024 * 1024
ALLOWED_DEVICE_TOOLS = {
    "self.get_device_status",
    "self.audio_speaker.set_volume",
    "self.audio_speaker.get_volume",
    "self.screen.set_brightness",
    "self.screen.set_theme",
    "self.gif.set_gif_mode",
    "self.display.set_mode",
    "self.camera.take_photo",
    "take_photo",
    "take_screenshot",
    "self.AEC.set_mode",
    "self.AEC.get_mode",
}


def _device_tool_key(name: str) -> str:
    return "".join(character for character in name.lower() if character.isalnum())


def _is_photo_like_device_tool(name: str) -> bool:
    return _device_tool_key(name) in {
        "takephoto",
        "selfcameratakephoto",
        "cameratakephoto",
        "takescreenshot",
        "selfcameratakescreenshot",
        "cameratakescreenshot",
    }


DEVICE_TOOL_ALIASES = {
    _device_tool_key("self.setvolume"): ("self.audio_speaker.set_volume",),
    _device_tool_key("setvolume"): ("self.audio_speaker.set_volume",),
    _device_tool_key("set_volume"): ("self.audio_speaker.set_volume",),
    _device_tool_key("self.getvolume"): ("self.audio_speaker.get_volume",),
    _device_tool_key("getvolume"): ("self.audio_speaker.get_volume",),
    _device_tool_key("get_volume"): ("self.audio_speaker.get_volume",),
    _device_tool_key("self.camera.take_photo"): ("take_photo", "self.camera.take_photo"),
    _device_tool_key("camera.take_photo"): ("take_photo", "self.camera.take_photo"),
    _device_tool_key("takephoto"): ("take_photo",),
    _device_tool_key("self.camera.take_screenshot"): ("take_screenshot",),
    _device_tool_key("camera.take_screenshot"): ("take_screenshot",),
    _device_tool_key("takescreenshot"): ("take_screenshot",),
}


def _resolve_device_tool_name(name: str, exposed_tools: dict[str, dict[str, Any]]) -> str:
    exposed_names = set(exposed_tools)
    candidates = [name, *DEVICE_TOOL_ALIASES.get(_device_tool_key(name), ())]

    if exposed_names:
        for candidate in candidates:
            if candidate in exposed_names and candidate in ALLOWED_DEVICE_TOOLS:
                return candidate
        exposed_by_key = {
            _device_tool_key(exposed_name): exposed_name
            for exposed_name in exposed_names
            if exposed_name in ALLOWED_DEVICE_TOOLS
        }
        for candidate in candidates:
            exposed_name = exposed_by_key.get(_device_tool_key(candidate))
            if exposed_name:
                return exposed_name

    for candidate in candidates:
        if candidate in ALLOWED_DEVICE_TOOLS:
            return candidate
    return name


def _require_pairing_token(
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> None:
    headers = {"authorization": authorization or "", "x-api-key": x_api_key or ""}
    if not token_is_valid(extract_request_token(headers)):
        raise HTTPException(status_code=401, detail="Invalid pairing token")


def _public_base_url(request: Request) -> str:
    configured = os.environ.get("CONTENT_BUILDER_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return _public_base_url_for_remote(configured, str(getattr(getattr(request, "client", None), "host", "") or ""))
    base = str(request.base_url).rstrip("/")
    return _public_base_url_for_remote(base, str(getattr(getattr(request, "client", None), "host", "") or ""))


def _public_ws_url(request: Request) -> str:
    base = _public_base_url(request)
    return base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


def _public_base_url_from_websocket(websocket: WebSocket) -> str:
    remote_host = str(getattr(getattr(websocket, "client", None), "host", "") or "")
    configured = os.environ.get("CONTENT_BUILDER_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return _public_base_url_for_remote(configured, remote_host)
    return _public_base_url_for_remote(str(websocket.url), remote_host)


def _public_base_url_for_remote(url: str, remote_host: str = "") -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    local_candidates = _local_ipv4_candidates()
    if _host_is_not_device_reachable(host) or _host_is_stale_private_ipv4(host, local_candidates):
        host = _best_local_ip_for_remote(remote_host, local_candidates) or host
    host_part = f"[{host}]" if ":" in host and not host.startswith("[") else host
    base = f"{parts.scheme}://{host_part}{port}"
    return base.replace("wss://", "https://", 1).replace("ws://", "http://", 1)


def _host_is_not_device_reachable(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized in {"", "localhost", "0.0.0.0", "::", "::1"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _local_ip_for_remote(remote_host: str) -> str:
    remote = remote_host.strip().strip("[]")
    if not remote:
        return ""
    try:
        family = socket.AF_INET6 if ":" in remote else socket.AF_INET
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.connect((remote, 9))
            local = sock.getsockname()[0]
        if local and not _host_is_not_device_reachable(local):
            return local
    except OSError:
        return ""
    return ""


def _best_local_ip_for_remote(remote_host: str, candidates: list[str] | None = None) -> str:
    routed_ip = _local_ip_for_remote(remote_host)
    if routed_ip:
        return routed_ip
    candidates = candidates if candidates is not None else _local_ipv4_candidates()
    if not candidates:
        return ""
    remote = _parse_ipv4(remote_host)

    def score(candidate: str) -> int:
        candidate_ip = _parse_ipv4(candidate)
        if candidate_ip is None:
            return -100
        value = 0
        if candidate_ip.is_private:
            value += 20
        if str(candidate_ip).endswith(".1"):
            value -= 5
        if remote is not None and _same_ipv4_24(candidate_ip, remote):
            value += 100
        return value

    return max(candidates, key=score)


def _host_is_stale_private_ipv4(host: str, local_candidates: list[str]) -> bool:
    address = _parse_ipv4(host)
    return bool(address and address.is_private and str(address) not in local_candidates)


def _local_ipv4_candidates() -> list[str]:
    candidates: list[str] = []
    for candidate in [*_local_ipv4_candidates_from_hostname(), *_local_ipv4_candidates_from_ipconfig()]:
        if candidate not in candidates and _usable_ipv4(candidate):
            candidates.append(candidate)
    return candidates


def _local_ipv4_candidates_from_hostname() -> list[str]:
    try:
        return [info[-1][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        return []


def _local_ipv4_candidates_from_ipconfig() -> list[str]:
    if os.name != "nt":
        return []
    try:
        output = subprocess.check_output(["ipconfig"], text=True, encoding="utf-8", errors="ignore")
    except (OSError, subprocess.SubprocessError):
        return []
    return re.findall(r"IPv4[^\r\n:：]*[:：]\s*([0-9]+(?:\.[0-9]+){3})", output)


def _usable_ipv4(candidate: str) -> bool:
    address = _parse_ipv4(candidate)
    return bool(address and not (address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified))


def _parse_ipv4(candidate: str) -> ipaddress.IPv4Address | None:
    try:
        address = ipaddress.ip_address(candidate.strip().strip("[]"))
    except ValueError:
        return None
    return address if isinstance(address, ipaddress.IPv4Address) else None


def _same_ipv4_24(left: ipaddress.IPv4Address, right: ipaddress.IPv4Address) -> bool:
    return str(left).split(".")[:3] == str(right).split(".")[:3]


def _photo_payload_has_file(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    file_payload = payload.get("file")
    return isinstance(file_payload, dict) and bool(file_payload.get("path"))


def _extract_ws_token(websocket: WebSocket, query_token: str) -> str:
    if query_token:
        return query_token
    return extract_request_token(dict(websocket.headers))


def _vision_token_valid(
    *,
    authorization: str | None = None,
    x_api_key: str | None = None,
    token: str = "",
) -> bool:
    candidate = token or extract_request_token({"authorization": authorization or "", "x-api-key": x_api_key or ""})
    return token_is_valid(candidate)


def _agent_server_url() -> str:
    return os.environ.get("CONTENT_BUILDER_AGENT_SERVER_URL", "http://127.0.0.1:2025").rstrip("/")


def _agent_thread_id(thread_id: str) -> str:
    candidate = (thread_id or DEFAULT_THREAD_ID).strip()
    try:
        return str(uuid.UUID(candidate))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"content-builder-agent:xiaozhi:{candidate}"))


def _json_line(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _stream_part_get(part: Any, key: str, default: Any = None) -> Any:
    if isinstance(part, dict):
        return part.get(key, default)
    return getattr(part, key, default)


def _text_from_agent_server_messages(data: Any) -> str:
    if isinstance(data, list):
        if len(data) == 2 and isinstance(data[0], dict) and isinstance(data[1], dict):
            metadata = data[1]
            if metadata.get("langgraph_node") == "tools":
                return ""
            items = [data[0]]
        else:
            items = data
    else:
        items = [data]

    pieces: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        message_type = str(item.get("type") or item.get("role") or "").lower()
        if message_type in {"human", "user", "tool", "toolmessage"}:
            continue
        if item.get("tool_call_id"):
            continue
        content = item.get("content")
        text = text_from_content(content)
        if not text:
            text = str(item.get("text") or "")
        if text:
            pieces.append(text)
    return "".join(pieces)


def _agent_input_for_xiaozhi_turn(session: "XiaozhiSession", transcript: str) -> str:
    return transcript


async def _final_text_from_agent_server_state(client: Any, thread_id: str, headers: dict[str, str]) -> str:
    with contextlib.suppress(Exception):
        state = await client.threads.get_state(thread_id)
        values = state.get("values") if isinstance(state, dict) else getattr(state, "values", None)
        return final_text_from_state(values or state)
    return ""


class OpusCodec:
    """Small optional wrapper around opuslib.

    Importing this module must not require libopus. The gateway can still serve
    OTA/MCP/status routes when audio dependencies are missing, and the active
    voice turn reports a clear error.
    """

    def __init__(self, *, pcm_sample_rate: int = OPUS_OUTPUT_SAMPLE_RATE) -> None:
        try:
            import opuslib  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised by deployment diagnostics
            raise RuntimeError(
                "Opus codec is unavailable. Install the Python opuslib package and libopus runtime "
                "before using Xiaozhi hardware audio."
            ) from exc

        self._opuslib = opuslib
        self.decoder = opuslib.Decoder(OPUS_INPUT_SAMPLE_RATE, OPUS_CHANNELS)
        self.encoder = opuslib.Encoder(OPUS_OUTPUT_SAMPLE_RATE, OPUS_CHANNELS, opuslib.APPLICATION_AUDIO)
        self.input_frame_samples = OPUS_INPUT_SAMPLE_RATE * OPUS_FRAME_DURATION_MS // 1000
        self.output_frame_samples = OPUS_OUTPUT_SAMPLE_RATE * OPUS_FRAME_DURATION_MS // 1000
        self.output_frame_bytes = self.output_frame_samples * OPUS_CHANNELS * OPUS_SAMPLE_WIDTH
        self.pcm_sample_rate = pcm_sample_rate
        self._ratecv_state: Any = None
        self._pcm_remainder = bytearray()

    def decode(self, frame: bytes) -> bytes:
        return self.decoder.decode(frame, self.input_frame_samples, decode_fec=False)

    def encode_pcm_stream(self, pcm: bytes) -> list[bytes]:
        self._pcm_remainder.extend(self._resample_pcm(pcm))
        frames: list[bytes] = []
        while len(self._pcm_remainder) >= self.output_frame_bytes:
            chunk = bytes(self._pcm_remainder[: self.output_frame_bytes])
            del self._pcm_remainder[: self.output_frame_bytes]
            frames.append(self.encoder.encode(chunk, self.output_frame_samples))
        return frames

    def flush(self) -> list[bytes]:
        if not self._pcm_remainder:
            return []
        padded = bytes(self._pcm_remainder).ljust(self.output_frame_bytes, b"\x00")
        self._pcm_remainder.clear()
        return [self.encoder.encode(padded, self.output_frame_samples)]

    def _resample_pcm(self, pcm: bytes) -> bytes:
        if not pcm or self.pcm_sample_rate == OPUS_OUTPUT_SAMPLE_RATE:
            return pcm
        converted, self._ratecv_state = audioop.ratecv(
            pcm,
            OPUS_SAMPLE_WIDTH,
            OPUS_CHANNELS,
            self.pcm_sample_rate,
            OPUS_OUTPUT_SAMPLE_RATE,
            self._ratecv_state,
        )
        return converted


class XiaozhiWebSocketOpusPlayer:
    """VoiceResponseSpeaker PCM adapter that sends Xiaozhi Opus frames."""

    def __init__(self, session: "XiaozhiSession") -> None:
        self.session = session
        self.codec = OpusCodec(pcm_sample_rate=session.tts_sample_rate)
        self.started = False
        self._pending_segment_text = ""
        self._segment_open = False
        self._send_queue: asyncio.Queue[tuple[str, bytes | str | None]] = asyncio.Queue()
        self._send_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self.started = True
        if self._send_task is None or self._send_task.done():
            self._send_task = asyncio.create_task(self._send_worker())

    async def send_segment_text(self, text: str) -> None:
        self._pending_segment_text = text

    async def enqueue_pcm(self, pcm_data: bytes, *, segment_start: bool = False) -> None:
        if segment_start:
            await self._queue_segment_end()
            await self._send_queue.put(("start", self._pending_segment_text))
            self._pending_segment_text = ""
            self._segment_open = True
        for frame in self.codec.encode_pcm_stream(pcm_data):
            await self._send_queue.put(("frame", frame))

    async def mark_segment_end(self) -> None:
        for frame in self.codec.flush():
            await self._send_queue.put(("frame", frame))
        await self._queue_segment_end()

    async def _queue_segment_end(self) -> None:
        if self._segment_open:
            await self._send_queue.put(("end", None))
            self._segment_open = False

    async def send_emotion(self, emotion: dict[str, str | float]) -> None:
        await self.session.send_llm_emotion(
            str(emotion.get("emotion_en") or "neutral"),
            text=str(emotion.get("text") or ""),
        )
        await self.session.publish(
            "emotion",
            {"thread_id": self.session.thread_id, "session_id": self.session.session_id, **emotion},
        )

    async def drain(self) -> None:
        await self._send_queue.join()
        if self._send_task and self._send_task.done():
            self._send_task.result()

    async def close(self) -> None:
        await self.drain()
        if self._send_task and not self._send_task.done():
            await self._send_queue.put(("close", None))
            await self._send_task

    async def _send_worker(self) -> None:
        segment_text = ""
        segment_buffer: list[bytes] = []
        segment_started = False
        paced_start = 0.0
        play_position_ms = 0

        async def start_segment() -> None:
            nonlocal segment_started, paced_start, play_position_ms
            if segment_started:
                return
            await self.session.send_tts_sentence_start(segment_text)
            for buffered_frame in segment_buffer:
                await self.session.send_binary(buffered_frame)
            segment_buffer.clear()
            segment_started = True
            paced_start = time.perf_counter()
            play_position_ms = OPUS_FRAME_DURATION_MS

        async def send_paced_frame(frame: bytes) -> None:
            nonlocal play_position_ms
            expected_time = paced_start + (play_position_ms / 1000)
            delay = expected_time - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            await self.session.send_binary(frame)
            play_position_ms += OPUS_FRAME_DURATION_MS

        async def finish_segment() -> None:
            nonlocal segment_text, segment_started, paced_start, play_position_ms
            if not segment_started and not segment_buffer:
                segment_text = ""
                return
            await start_segment()
            if XIAOZHI_TTS_TAIL_DRAIN_MS > 0:
                await asyncio.sleep(XIAOZHI_TTS_TAIL_DRAIN_MS / 1000)
            await self.session.send_tts_sentence_end()
            segment_text = ""
            segment_started = False
            paced_start = 0.0
            play_position_ms = 0

        while True:
            kind, payload = await self._send_queue.get()
            try:
                if kind == "close":
                    await finish_segment()
                    return
                if kind == "start":
                    await finish_segment()
                    segment_text = str(payload or "")
                    segment_buffer = []
                    segment_started = False
                    paced_start = 0.0
                    play_position_ms = 0
                    continue
                if kind == "end":
                    await finish_segment()
                    continue
                if kind != "frame" or not isinstance(payload, bytes):
                    continue
                if not segment_started:
                    segment_buffer.append(payload)
                    if len(segment_buffer) >= XIAOZHI_TTS_PREBUFFER_FRAMES:
                        await start_segment()
                    continue
                await send_paced_frame(payload)
            finally:
                self._send_queue.task_done()

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
        await self.send_json(
            {
                "type": "mcp",
                "payload": {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params or {},
                    "id": request_id,
                },
            }
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout)
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
                if _photo_payload_has_file(tool_result):
                    return tool_result
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
                if not next_cursor or next_cursor in seen_cursors:
                    break
                seen_cursors.add(next_cursor)
                cursor = next_cursor

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

    @contextlib.asynccontextmanager
    async def subscribe(self, thread_id: str):
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        self.listeners[thread_id].add(queue)
        try:
            yield queue
        finally:
            self.listeners[thread_id].discard(queue)


session_manager = XiaozhiSessionManager()


@router.get("/ota")
@router.post("/ota")
async def get_ota_config(request: Request, token: str = Query("")) -> dict[str, Any]:
    if not await asyncio.to_thread(token_is_valid, token):
        raise HTTPException(status_code=401, detail="Invalid pairing token")
    thread_id = request.query_params.get("thread_id") or XIAOZHI_DEFAULT_THREAD_ID
    ws_base = _public_ws_url(request)
    return {
        "websocket": {
            "url": f"{ws_base}/api/xiaozhi/v1/ws?thread_id={thread_id}",
            "token": token,
            "version": 1,
        },
        "server_time": {
            "timestamp": int(time.time() * 1000),
            "timezone_offset": -time.timezone // 60,
        },
        "firmware": None,
    }


@router.get("/v1/status", dependencies=[Depends(_require_pairing_token)])
async def get_status() -> dict[str, Any]:
    return {
        "sessions": [
            {
                "session_id": session.session_id,
                "thread_id": session.thread_id,
                "device_id": session.device_id,
                "client_id": session.client_id,
                "tools": sorted(session.tools),
            }
            for session in session_manager.by_session.values()
        ]
    }


@router.get("/v1/threads/{thread_id}/events")
async def stream_thread_events(
    thread_id: str,
    token: str = Query(""),
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    headers = {"authorization": authorization or "", "x-api-key": x_api_key or ""}
    candidate = token or extract_request_token(headers)
    if not await asyncio.to_thread(token_is_valid, candidate):
        raise HTTPException(status_code=401, detail="Invalid pairing token")

    async def events():
        async with session_manager.subscribe(thread_id) as queue:
            yield _json_line("ready", {"thread_id": thread_id})
            while True:
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=20)
                    yield _json_line(event, payload)
                except asyncio.TimeoutError:
                    yield _json_line("ping", {"thread_id": thread_id, "time": int(time.time())})

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("/v1/sessions/{thread_id}/mcp/tools/call", dependencies=[Depends(_require_pairing_token)])
async def call_device_tool(thread_id: str, payload: Annotated[dict[str, Any], Body(default_factory=dict)]) -> dict[str, Any]:
    session = session_manager.active_for_thread(thread_id)
    if session is None:
        raise HTTPException(status_code=404, detail="No connected Xiaozhi device for this thread")
    name = str(payload.get("name") or "")
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    original_user_text = str(getattr(session, "current_user_transcript", "") or "")
    if original_user_text and name in {"self.camera.take_photo", "take_photo", "take_screenshot"}:
        arguments = {**arguments, "question": original_user_text}
    try:
        if _is_photo_like_device_tool(name) and isinstance(session, XiaozhiSession):
            return await session.call_photo_tool(name, arguments)
        return await session.call_tool(name, arguments)
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=f"Xiaozhi device tool timed out: {name}") from exc


@compat_router.get("/mcp/vision/explain")
async def explain_vision_health() -> Response:
    return Response("MCP Vision interface is running", media_type="text/plain")


async def _handle_vision_upload(
    *,
    route_name: str,
    thread_id: str,
    token: str,
    authorization: str | None,
    x_api_key: str | None,
    file: UploadFile | None,
    question: str,
) -> JSONResponse:
    if not await asyncio.to_thread(_vision_token_valid, authorization=authorization, x_api_key=x_api_key, token=token):
        raise HTTPException(status_code=401, detail="Invalid vision token")
    if file is None:
        raise HTTPException(status_code=400, detail="Missing image file")
    content = await file.read(MAX_IMAGE_BYTES + 1)
    upload_payload, _image_path = await asyncio.to_thread(_save_vision_upload_sync, thread_id, content, question)
    active_session = session_manager.active_for_thread(thread_id)
    if active_session is not None:
        await active_session.remember_photo_upload(upload_payload)
    logger.info("Xiaozhi photo uploaded via %s thread=%s file=%s", route_name, thread_id, upload_payload["file"]["path"])
    await asyncio.to_thread(
        save_thread_history_snapshot,
        thread_id,
        event={"type": "xiaozhi_photo_upload", "question": question, "file": upload_payload["file"]},
    )
    await session_manager.publish(thread_id, "photo_uploaded", upload_payload)
    return JSONResponse(upload_payload)


@compat_router.post("/mcp/vision/explain")
async def explain_vision_compat(
    thread_id: str = Query(XIAOZHI_DEFAULT_THREAD_ID),
    token: str = Query(""),
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
    file: Annotated[UploadFile | None, File()] = None,
    question: Annotated[str, Form()] = "",
) -> JSONResponse:
    return await _handle_vision_upload(
        route_name="mcp/vision/explain",
        thread_id=thread_id,
        token=token,
        authorization=authorization,
        x_api_key=x_api_key,
        file=file,
        question=question,
    )


@router.post("/v1/vision/explain")
async def explain_vision(
    thread_id: str = Query(XIAOZHI_DEFAULT_THREAD_ID),
    token: str = Query(""),
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
    file: Annotated[UploadFile | None, File()] = None,
    question: Annotated[str, Form()] = "",
) -> JSONResponse:
    return await _handle_vision_upload(
        route_name="api/vision/explain",
        thread_id=thread_id,
        token=token,
        authorization=authorization,
        x_api_key=x_api_key,
        file=file,
        question=question,
    )


@router.post("/v1/vision/upload")
async def upload_vision(
    thread_id: str = Query(XIAOZHI_DEFAULT_THREAD_ID),
    token: str = Query(""),
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
    file: Annotated[UploadFile | None, File()] = None,
    question: Annotated[str, Form()] = "",
) -> JSONResponse:
    if not await asyncio.to_thread(_vision_token_valid, authorization=authorization, x_api_key=x_api_key, token=token):
        raise HTTPException(status_code=401, detail="Invalid vision token")
    if file is None:
        raise HTTPException(status_code=400, detail="Missing image file")
    content = await file.read(MAX_IMAGE_BYTES + 1)
    payload, _image_path = await asyncio.to_thread(_save_vision_upload_sync, thread_id, content, question)
    active_session = session_manager.active_for_thread(thread_id)
    if active_session is not None:
        await active_session.remember_photo_upload(payload)
    logger.info("Xiaozhi photo uploaded via upload thread=%s file=%s", thread_id, payload["file"]["path"])
    await session_manager.publish(thread_id, "photo_uploaded", payload)
    return JSONResponse(payload)


@router.post("/v1/vision/analyze", dependencies=[Depends(_require_pairing_token)])
async def analyze_uploaded_vision(payload: Annotated[dict[str, Any], Body(default_factory=dict)]) -> JSONResponse:
    upload_thread_id = str(payload.get("thread_id") or XIAOZHI_DEFAULT_THREAD_ID)
    question = str(payload.get("question") or "请描述这张图片。")
    file_payload = payload.get("file") if isinstance(payload.get("file"), dict) else {}
    relative_path = str(payload.get("path") or file_payload.get("path") or "")
    if not relative_path:
        raise HTTPException(status_code=400, detail="Missing uploaded image path")
    try:
        image_path = resolve_thread_file(upload_thread_id, relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="Uploaded image file was not found")

    result_text = await asyncio.to_thread(_explain_image_sync, question, image_path)
    result = {
        "success": True,
        "result": result_text,
        "thread_id": upload_thread_id,
        "question": question,
        "file": {"path": relative_path, "mime_type": str(file_payload.get("mime_type") or "image/jpeg")},
    }
    await asyncio.to_thread(
        save_thread_history_snapshot,
        upload_thread_id,
        event={"type": "xiaozhi_photo", "question": question, "file": result["file"], "result": result_text},
    )
    await session_manager.publish(upload_thread_id, "photo", result)
    return JSONResponse(result)


def _save_vision_upload_sync(thread_id: str, content: bytes, question: str) -> tuple[dict[str, Any], Path]:
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the 20 MB upload limit")
    if not content:
        raise HTTPException(status_code=400, detail="Empty image")

    paths = thread_paths(thread_id)
    image_path = paths.uploads / "images" / f"{uuid.uuid4().hex}.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(content)
    virtual_path = f"uploads/images/{image_path.name}"
    payload = {
        "success": True,
        "thread_id": thread_id,
        "question": question,
        "file": {"path": virtual_path, "mime_type": "image/jpeg"},
    }
    return payload, image_path


def _explain_image_sync(question: str, image_path: Path) -> str:
    config = load_main_config()
    model = create_qwen_model(config.model)
    content = build_user_content(question, [image_path])
    response = model.invoke([HumanMessage(content=content)])
    result = getattr(response, "content", response)
    if isinstance(result, list):
        return "\n".join(str(item.get("text", "")) for item in result if isinstance(item, dict)).strip()
    return str(result).strip()


@router.websocket("/v1/ws")
async def websocket_endpoint(websocket: WebSocket, thread_id: str = Query(XIAOZHI_DEFAULT_THREAD_ID), token: str = Query("")) -> None:
    await websocket.accept()
    if not await asyncio.to_thread(token_is_valid, _extract_ws_token(websocket, token)):
        await websocket.close(code=4401, reason="Invalid pairing token")
        return

    device_id = websocket.headers.get("Device-Id", "")
    client_id = websocket.headers.get("Client-Id", "")
    protocol_version = websocket.headers.get("Protocol-Version", "1")
    try:
        version = int(protocol_version)
    except ValueError:
        version = 1

    session = XiaozhiSession(
        websocket=websocket,
        thread_id=thread_id,
        device_id=device_id,
        client_id=client_id,
        version=version,
    )
    session_manager.register(session)
    await session.publish(
        "connected",
        {"thread_id": thread_id, "session_id": session.session_id, "device_id": device_id, "client_id": client_id},
    )

    try:
        initial = await websocket.receive_text()
        hello = json.loads(initial)
        if hello.get("type") != "hello":
            await websocket.close(code=4400, reason="Expected hello")
            return
        await session.send_json(
            {
                "type": "hello",
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": OPUS_OUTPUT_SAMPLE_RATE,
                    "channels": OPUS_CHANNELS,
                    "frame_duration": OPUS_FRAME_DURATION_MS,
                },
            }
        )
        asyncio.create_task(session.initialize_mcp(_public_base_url_from_websocket(websocket)))

        while True:
            message = await websocket.receive()
            if "bytes" in message and message["bytes"] is not None:
                audio_frame = session.unpack_audio(message["bytes"])
                if audio_frame:
                    if session.manual_listen_started:
                        session.audio_frames.append(audio_frame)
                    else:
                        await _handle_device_audio(session, audio_frame)
                continue
            if "text" not in message or message["text"] is None:
                continue
            await _handle_device_text(session, message["text"])
    except WebSocketDisconnect as exc:
        await session.publish("disconnected", {"thread_id": thread_id, "session_id": session.session_id, "reason": f"disconnect:{exc.code}"})
    except Exception as exc:
        await session.publish("disconnected", {"thread_id": thread_id, "session_id": session.session_id, "reason": str(exc)})
    finally:
        session_manager.unregister(session)


async def _handle_device_audio(session: XiaozhiSession, audio_frame: bytes) -> None:
    """Handle full-duplex Xiaozhi audio that arrives without a listen/stop event."""

    try:
        if session.auto_vad_codec is None:
            session.auto_vad_codec = OpusCodec(pcm_sample_rate=OPUS_INPUT_SAMPLE_RATE)
        pcm = session.auto_vad_codec.decode(audio_frame)
    except Exception as exc:
        await session.publish(
            "hardware_error",
            {"thread_id": session.thread_id, "message": f"Failed to decode Xiaozhi audio frame: {exc}"},
        )
        return

    if not pcm:
        return

    now = time.monotonic()
    rms = audioop.rms(pcm, OPUS_SAMPLE_WIDTH)
    is_voice = rms >= XIAOZHI_AUTO_VAD_RMS_THRESHOLD

    if is_voice:
        if session.server_is_speaking and session.agent_task and not session.agent_task.done():
            session.agent_task.cancel()
            with contextlib.suppress(Exception):
                await session.send_json({"type": "tts", "state": "stop"})
            await session.publish("abort", {"thread_id": session.thread_id, "reason": "barge-in"})

        if not session.auto_speech_started:
            session.auto_speech_started = True
            session.auto_speech_started_at = now
            session.auto_speech_frames.clear()
            await session.publish("listening", {"thread_id": session.thread_id, "state": "auto_speech_start", "rms": rms})

        session.auto_last_voice_at = now
        session.auto_speech_frames.append(audio_frame)
        return

    if not session.auto_speech_started:
        return

    session.auto_speech_frames.append(audio_frame)
    speech_ms = int((now - session.auto_speech_started_at) * 1000)
    silence_ms = int((now - session.auto_last_voice_at) * 1000)
    if speech_ms < XIAOZHI_AUTO_VAD_MIN_SPEECH_MS:
        return
    if silence_ms < XIAOZHI_AUTO_VAD_SILENCE_MS and speech_ms < XIAOZHI_AUTO_VAD_MAX_SPEECH_MS:
        return

    frames = list(session.auto_speech_frames)
    session.reset_auto_vad()
    await session.publish(
        "listening",
        {"thread_id": session.thread_id, "state": "auto_speech_stop", "speech_ms": speech_ms, "silence_ms": silence_ms},
    )
    if session.agent_task and not session.agent_task.done():
        await session.publish("listening", {"thread_id": session.thread_id, "state": "auto_speech_dropped_busy"})
        return
    session.agent_task = asyncio.create_task(_run_voice_turn_from_audio(session, frames))


async def _handle_device_text(session: XiaozhiSession, text: str) -> None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        await session.publish("hardware_error", {"thread_id": session.thread_id, "message": "Invalid JSON from device"})
        return

    message_type = payload.get("type")
    if message_type == "listen":
        state = payload.get("state")
        session.current_listen_mode = str(payload.get("mode") or session.current_listen_mode)
        if state == "start":
            session.manual_listen_started = session.current_listen_mode == "manual"
            if session.manual_listen_started:
                session.reset_auto_vad()
            session.audio_frames.clear()
            await session.publish(
                "listening",
                {"thread_id": session.thread_id, "state": "start", "mode": session.current_listen_mode},
            )
        elif state == "stop":
            manual_frames = list(session.audio_frames)
            session.manual_listen_started = False
            session.audio_frames.clear()
            if manual_frames:
                if session.agent_task and not session.agent_task.done():
                    session.agent_task.cancel()
                session.agent_task = asyncio.create_task(_run_voice_turn_from_audio(session, manual_frames))
        elif state == "detect":
            session.manual_listen_started = False
            session.audio_frames.clear()
            session.reset_auto_vad()
            text = str(payload.get("text") or "").strip()
            await session.publish("wake", {"thread_id": session.thread_id, "text": text})
            if text:
                if session.agent_task and not session.agent_task.done():
                    session.agent_task.cancel()
                session.agent_task = asyncio.create_task(_run_text_turn(session, text))
    elif message_type == "abort":
        if session.agent_task and not session.agent_task.done():
            session.agent_task.cancel()
        await session.send_json({"type": "tts", "state": "stop"})
        await session.publish("abort", {"thread_id": session.thread_id, "reason": payload.get("reason", "")})
    elif message_type == "mcp":
        mcp_payload = payload.get("payload")
        if isinstance(mcp_payload, dict):
            await session.handle_mcp_payload(mcp_payload)


async def _run_text_turn(session: XiaozhiSession, transcript: str) -> None:
    try:
        config = await asyncio.to_thread(load_main_config)
        ensure_voice_ready(config)
        await session.send_json({"type": "stt", "text": transcript})
        await session.publish("message", {"thread_id": session.thread_id, "role": "human", "content": transcript})
        await asyncio.to_thread(
            save_thread_history_snapshot,
            session.thread_id,
            messages=[{"type": "human", "content": transcript}],
            metadata={"source": "xiaozhi-text"},
        )

        session.server_is_speaking = True
        await session.send_json({"type": "tts", "state": "start"})
        await _run_agent_tts_turn(session, transcript, config)
        await session.send_json({"type": "tts", "state": "stop"})
        session.server_is_speaking = False
    except asyncio.CancelledError:
        session.server_is_speaking = False
        await session.send_json({"type": "tts", "state": "stop"})
        raise
    except Exception as exc:
        session.server_is_speaking = False
        message = f"Xiaozhi text turn failed: {exc}"
        await session.publish("hardware_error", {"thread_id": session.thread_id, "message": message})
        with contextlib.suppress(Exception):
            await session.send_json({"type": "alert", "status": "error", "message": message, "emotion": "sad"})


async def _run_voice_turn_from_audio(session: XiaozhiSession, opus_frames: list[bytes]) -> None:
    try:
        wav_path = await asyncio.to_thread(_opus_frames_to_wav, opus_frames)
        config = await asyncio.to_thread(load_main_config)
        ensure_voice_ready(config)
        from content_builder.server.api import _ensure_asr_manager_sync  # reuse existing lazy singleton

        asr_manager = await asyncio.to_thread(_ensure_asr_manager_sync, force_retry=True)
        transcript = await asr_manager.recognize_file(wav_path, session_id=session.thread_id)
        transcript = transcript.strip()
        if not transcript:
            await session.publish("stt", {"thread_id": session.thread_id, "text": ""})
            return

        await session.send_json({"type": "stt", "text": transcript})
        await session.publish("message", {"thread_id": session.thread_id, "role": "human", "content": transcript})
        await asyncio.to_thread(
            save_thread_history_snapshot,
            session.thread_id,
            messages=[{"type": "human", "content": transcript}],
            metadata={"source": "xiaozhi-hardware"},
        )

        session.server_is_speaking = True
        await session.send_json({"type": "tts", "state": "start"})
        await _run_agent_tts_turn(session, transcript, config)
        await session.send_json({"type": "tts", "state": "stop"})
        session.server_is_speaking = False
    except asyncio.CancelledError:
        session.server_is_speaking = False
        await session.send_json({"type": "tts", "state": "stop"})
        raise
    except Exception as exc:
        session.server_is_speaking = False
        message = f"Xiaozhi voice turn failed: {exc}"
        await session.publish("hardware_error", {"thread_id": session.thread_id, "message": message})
        with contextlib.suppress(Exception):
            await session.send_json({"type": "alert", "status": "error", "message": message, "emotion": "sad"})


async def _run_agent_tts_turn(session: XiaozhiSession, transcript: str, config: Any) -> None:
    session.current_user_transcript = transcript
    session.tts_sample_rate = int(getattr(config.voice.tts, "sample_rate", 24000) or 24000)
    speaker = VoiceResponseSpeaker(config.voice, config.secrets, pcm_player=XiaozhiWebSocketOpusPlayer(session))
    await speaker.start()
    delta_filter = MainTokenDeltaFilter()
    echo_filter = UserEchoFilter(transcript)
    saw_main_token = False
    final_text = ""
    client = get_client(url=_agent_server_url(), api_key=pairing_token(), timeout=None)
    agent_thread_id = _agent_thread_id(session.thread_id)
    agent_input = _agent_input_for_xiaozhi_turn(session, transcript)
    logger.info("Xiaozhi ASR transcript for thread %s -> agent thread %s: %s", session.thread_id, agent_thread_id, transcript)
    try:
        async for part in client.runs.stream(
            agent_thread_id,
            "content_writer",
            input={"messages": [{"role": "user", "content": agent_input}]},
            stream_mode=["messages", "updates", "values", "custom", "tasks"],
            stream_subgraphs=True,
            if_not_exists="create",
            version="v2",
        ):
            event_type = str(_stream_part_get(part, "type", _stream_part_get(part, "event", "")))
            source = source_from_namespace(_stream_part_get(part, "ns", []))
            data = _stream_part_get(part, "data")
            logger.debug("Xiaozhi Agent Server event: type=%s source=%s", event_type, source)

            if event_type == "error":
                error_name = ""
                error_message = ""
                if isinstance(data, dict):
                    error_name = str(data.get("error") or "AgentServerError")
                    error_message = str(data.get("message") or "")
                detail = f"{error_name}: {error_message}".strip(": ")
                raise RuntimeError(f"Agent Server run failed: {detail or data}")

            if event_type in {"messages", "messages/partial", "messages/complete"}:
                if source != "main":
                    continue
                text = _text_from_agent_server_messages(data)
                delta = delta_filter.delta(text)
                if delta and not echo_filter.is_user_echo(delta):
                    saw_main_token = True
                    final_text += delta
                    await speaker.feed_token(delta)
                continue

            if event_type in {"updates", "values"}:
                text = final_text_from_update(data) if event_type == "updates" else final_text_from_state(data)
                if source == "main" and text:
                    final_text = text
                continue

            await session.publish(
                "agent_event",
                {"thread_id": session.thread_id, "type": event_type, "source": source, "text": text_from_content(data)},
            )
        if not final_text.strip():
            final_text = await _final_text_from_agent_server_state(client, agent_thread_id, {})
        if saw_main_token:
            await speaker.flush()
        elif final_text and not echo_filter.is_user_echo(final_text):
            await speaker.feed_token(final_text)
            await speaker.flush()
    finally:
        await speaker.close()
        await client.aclose()
    final_text = final_text.strip()
    if final_text:
        logger.info("Xiaozhi assistant final for thread %s -> agent thread %s: %s", session.thread_id, agent_thread_id, final_text[:500])
        await session.publish("message", {"thread_id": session.thread_id, "role": "assistant", "content": final_text})
        await asyncio.to_thread(
            save_thread_history_snapshot,
            session.thread_id,
            messages=[{"type": "human", "content": transcript}, {"type": "ai", "content": final_text}],
            metadata={"source": "xiaozhi-hardware"},
        )
    else:
        logger.warning("Xiaozhi Agent Server run finished without assistant text for thread %s", session.thread_id)


def _opus_frames_to_wav(opus_frames: list[bytes]) -> Path:
    if not opus_frames:
        raise RuntimeError("No audio frames received from Xiaozhi device")
    codec = OpusCodec()
    pcm = bytearray()
    for frame in opus_frames:
        pcm.extend(codec.decode(frame))
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
        wav_path = Path(temp_file.name)
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(OPUS_CHANNELS)
        wav_file.setsampwidth(2)
        wav_file.setframerate(OPUS_INPUT_SAMPLE_RATE)
        wav_file.writeframes(bytes(pcm))
    return wav_path
