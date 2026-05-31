"""控制台 PCM 流式播放器。

src 项目的真实语音播放链路是：
1. 后端 ``StreamTTS`` 生成 ``AudioData(data=pcm_bytes, format="pcm")``。
2. WebSocket 把 PCM chunk 发送给前端。
3. 前端 ``front/src/utils/pcmPlayer.js`` 把 24kHz/16bit/mono PCM
   转成 Web Audio buffer，并按队列顺序播放。

本项目首版是控制台交互，没有浏览器前端，所以这里用 Python 复刻一个本地
PCM 播放队列：收到一个 PCM chunk 就写入 sounddevice RawOutputStream。
这样可以保留 Qwen TTS 的流式 PCM 块，不再把裸 PCM 伪装成音频文件播放。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import tempfile
import wave
from dataclasses import dataclass
from typing import Final


SEGMENT_END: Final = object()


@dataclass(slots=True)
class PCMPlaybackConfig:
    """PCM 播放参数。

    Qwen ``qwen3-tts-flash`` 流式 TTS 默认返回 24kHz、16bit、单声道 PCM，
    这与 src 前端 ``PCMPlayer(sampleRate = 24000)`` 保持一致。
    """

    sample_rate: int = 24000
    channels: int = 1
    sample_width: int = 2


class PCMStreamPlayer:
    """异步 PCM 播放器。

    播放器内部维护一个异步队列。TTS 每产出一个 PCM chunk，就调用
    ``enqueue_pcm`` 入队；后台任务按顺序写入声卡输出流，避免多个音频块
    重叠播放。``drain`` 用于在一轮回复结束时等待队列播完。
    """

    def __init__(self, config: PCMPlaybackConfig | None = None, *, enabled: bool = True) -> None:
        self.config = config or PCMPlaybackConfig()
        self.enabled = enabled
        self._queue: asyncio.Queue[bytes | object | None] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._stream = None
        self._player_type: str | None = None
        self._closed = False
        self._available = False
        self._error_reported = False
        self._segment_open = False
        self._initial_buffer_bytes = (
            self.config.sample_rate
            * self.config.channels
            * self.config.sample_width
            // 5
        )

        if self.enabled:
            self._init_output_stream()

    def _init_output_stream(self) -> None:
        """初始化 sounddevice 原始 PCM 输出流。"""

        try:
            import sounddevice as sd

            self._stream = sd.RawOutputStream(
                samplerate=self.config.sample_rate,
                channels=self.config.channels,
                dtype="int16",
                blocksize=0,
            )
            self._stream.start()
            self._player_type = "sounddevice"
            self._available = True
            print(
                "[voice] PCM播放器已启动: "
                f"{self.config.sample_rate}Hz/{self.config.channels}ch/"
                f"{self.config.sample_width * 8}bit | backend=sounddevice | "
                f"初始缓冲={self._initial_buffer_bytes} bytes",
                flush=True,
            )
        except Exception as exc:
            if self._init_winsound_fallback():
                print(
                    "[voice] sounddevice PCM流播放器不可用，已切换到 Windows "
                    "winsound 句段缓冲兜底播放。"
                    f"原始错误：{exc}",
                    flush=True,
                )
                return

            self._available = False
            print(
                "[voice] PCM播放器初始化失败，无法本地播放声音："
                f"{exc}\n"
                "[voice] 请安装 sounddevice，或检查系统音频输出设备。",
                flush=True,
            )

    def _init_winsound_fallback(self) -> bool:
        """初始化 Windows 内置 WAV 播放兜底。

        winsound 不能直接播放裸 PCM，所以兜底模式会把每个 PCM chunk 临时包成
        WAV 文件顺序播放。它不如 sounddevice 连续，但能在未安装依赖时保持
        控制台可听见声音。
        """

        if sys.platform != "win32":
            return False
        try:
            import winsound  # noqa: F401

            self._player_type = "winsound"
            self._available = True
            return True
        except Exception:
            return False

    async def start(self) -> None:
        """启动后台播放任务。"""

        if not self.enabled or not self._available:
            return
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._play_worker())

    async def enqueue_pcm(self, pcm_data: bytes, *, segment_start: bool = False) -> None:
        """把一个 PCM chunk 放入播放队列。

        ``segment_start`` 对应 src 中 ``StreamTTS`` 每个句段的第一块音频：
        ``base.py`` 在流式 TTS 模式下只会给第一块 chunk 附带文本，后续 chunk
        的文本为空。控制台播放器据此知道“上一句段结束、新句段开始”。
        """

        if not pcm_data or self._closed:
            return
        if not self.enabled or not self._available:
            if not self._error_reported:
                print("[voice] 已生成PCM音频，但本地PCM播放器不可用，跳过播放。", flush=True)
                self._error_reported = True
            return
        if segment_start:
            await self.mark_segment_end()
            self._segment_open = True
        await self._queue.put(pcm_data)

    async def mark_segment_end(self) -> None:
        """标记当前 TTS 句段结束。"""

        if not self.enabled or not self._available:
            return
        if self._segment_open:
            await self._queue.put(SEGMENT_END)
            self._segment_open = False

    async def drain(self) -> None:
        """等待当前队列里的音频块全部播放完成。"""

        if not self.enabled or not self._available:
            return
        await self._queue.join()

    async def close(self) -> None:
        """等待播放完成并关闭声卡输出流。"""

        if self._closed:
            return
        self._closed = True
        await self.mark_segment_end()
        await self.drain()
        if self._worker_task:
            await self._queue.put(None)
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        if self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.stop()
            with contextlib.suppress(Exception):
                self._stream.close()

    async def _play_worker(self) -> None:
        """后台播放循环。"""

        if self._player_type == "winsound":
            await self._play_worker_winsound()
            return
        await self._play_worker_sounddevice()

    async def _play_worker_sounddevice(self) -> None:
        """sounddevice 后端：先做小缓冲，再连续写入声卡。"""

        pending = bytearray()
        started = False

        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    if pending:
                        self._write_chunk(bytes(pending))
                    return

                if item is SEGMENT_END:
                    if pending:
                        self._write_chunk(bytes(pending))
                        pending.clear()
                    started = False
                    continue

                pending.extend(item)
                if not started and len(pending) < self._initial_buffer_bytes:
                    continue

                started = True
                self._write_chunk(bytes(pending))
                pending.clear()
            finally:
                self._queue.task_done()

    async def _play_worker_winsound(self) -> None:
        """winsound 后端：按 TTS 句段合并 PCM，避免每个 chunk 一个小 WAV。"""

        segment = bytearray()

        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    if segment:
                        await asyncio.to_thread(self._play_chunk_with_winsound, bytes(segment))
                    return

                if item is SEGMENT_END:
                    if segment:
                        await asyncio.to_thread(self._play_chunk_with_winsound, bytes(segment))
                        segment.clear()
                    continue

                segment.extend(item)
            finally:
                self._queue.task_done()

    def _write_chunk(self, chunk: bytes) -> None:
        """向声卡写入一个 PCM chunk。"""

        if self._player_type == "sounddevice":
            if self._stream is None:
                return
            self._stream.write(chunk)
            return

        if self._player_type == "winsound":
            self._play_chunk_with_winsound(chunk)
            return

    def _play_chunk_with_winsound(self, chunk: bytes) -> None:
        """把 PCM chunk 包成临时 WAV 并用 winsound 同步播放。"""

        if not chunk:
            return
        import winsound

        temp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
                temp_path = temp_file.name

            with wave.open(temp_path, "wb") as wav_file:
                wav_file.setnchannels(self.config.channels)
                wav_file.setsampwidth(self.config.sample_width)
                wav_file.setframerate(self.config.sample_rate)
                wav_file.writeframes(chunk)

            winsound.PlaySound(temp_path, winsound.SND_FILENAME)
        finally:
            if temp_path:
                with contextlib.suppress(Exception):
                    os.remove(temp_path)
