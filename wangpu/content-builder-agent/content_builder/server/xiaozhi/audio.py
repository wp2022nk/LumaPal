"""Opus codec and PCM-to-Xiaozhi audio player."""

from __future__ import annotations

import asyncio
import audioop
import time
from typing import TYPE_CHECKING

from .constants import (
    OPUS_CHANNELS,
    OPUS_FRAME_DURATION_MS,
    OPUS_INPUT_SAMPLE_RATE,
    OPUS_OUTPUT_SAMPLE_RATE,
    OPUS_SAMPLE_WIDTH,
    XIAOZHI_TTS_PREBUFFER_FRAMES,
    XIAOZHI_TTS_TAIL_DRAIN_MS,
)

if TYPE_CHECKING:
    from .session import XiaozhiSession


def _root_attr(name: str, default):
    import content_builder.server.xiaozhi as xiaozhi_root

    return getattr(xiaozhi_root, name, default)


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
        self.codec = _root_attr("OpusCodec", OpusCodec)(pcm_sample_rate=session.tts_sample_rate)
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
            tail_drain_ms = _root_attr("XIAOZHI_TTS_TAIL_DRAIN_MS", XIAOZHI_TTS_TAIL_DRAIN_MS)
            if tail_drain_ms > 0:
                await asyncio.sleep(tail_drain_ms / 1000)
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
                    if len(segment_buffer) >= _root_attr("XIAOZHI_TTS_PREBUFFER_FRAMES", XIAOZHI_TTS_PREBUFFER_FRAMES):
                        await start_segment()
                    continue
                await send_paced_frame(payload)
            finally:
                self._send_queue.task_done()
