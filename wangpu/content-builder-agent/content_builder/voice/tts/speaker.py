"""把主智能体文本流交给参考项目 StreamTTS 处理。"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Protocol

from ...config import SecretsConfig, VoiceConfig
from ..emotion import EmotionExtractor
from .pcm_player import PCMPlaybackConfig, PCMStreamPlayer
from .stream_tts import StreamTTS


MAX_STREAM_TEXT_CHUNK = 12


class StreamTTSProtocol(Protocol):
    """测试用协议：真实环境使用复制自 ``wangpu/src/tts`` 的 StreamTTS。"""

    async def process_llm_stream(self, llm_stream, session_id=None, on_audio=None): ...

    def close(self) -> None: ...


class VoiceResponseSpeaker:
    """主智能体回复播报器。

    这里不再自写切段器，而是把主智能体 token 增量放进异步队列，再把这个队列
    包装成 async generator 交给复制过来的 ``StreamTTS.process_llm_stream``。
    因此文本分割、Markdown 清理、特殊字符删除、括号过滤等全部走参考项目
    ``base.py`` / ``utils.py`` / ``stream_tts.py`` 的实现。
    """

    def __init__(
        self,
        voice_config: VoiceConfig,
        secrets: SecretsConfig,
        *,
        tts_manager: StreamTTSProtocol | None = None,
        emotion_extractor: EmotionExtractor | None = None,
        pcm_player: PCMStreamPlayer | None = None,
    ) -> None:
        self.voice_config = voice_config
        self.emotion_extractor = emotion_extractor or EmotionExtractor()
        self.tts_manager = tts_manager or StreamTTS(self._build_stream_tts_config(voice_config, secrets))
        self.pcm_player = pcm_player or PCMStreamPlayer(
            PCMPlaybackConfig(
                sample_rate=voice_config.tts.sample_rate,
                channels=voice_config.tts.channels,
                sample_width=voice_config.tts.sample_width,
            ),
            enabled=voice_config.playback.enabled,
        )
        self._text_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._closed = False
        self.spoken_segments: list[str] = []
        self.emotion_events: list[dict[str, str | float]] = []

    def _build_stream_tts_config(self, voice_config: VoiceConfig, secrets: SecretsConfig) -> dict:
        """把项目配置转换成参考 StreamTTS 需要的 dict 形态。"""

        api_key = secrets.dashscope_api_key or secrets.qwen_api_key
        if not api_key:
            raise RuntimeError(
                "缺少 Qwen TTS API key。请在 secrets.local.yaml 中配置 "
                "dashscope.api_key，或配置 qwen.api_key 供 TTS 复用。"
            )

        # 这里仍允许 YAML 控制 Qwen 是否使用 provider 内部流式接口；无论该值
        # 是 true 还是 false，LLM token 都会以流的方式进入 StreamTTS，不会等
        # 主智能体整段回复结束才合成。
        return {
            "provider": "qwen",
            "qwen": {
                "api_key": api_key,
                "model": voice_config.tts.model,
                "voice": voice_config.tts.voice,
                "language_type": voice_config.tts.language_type,
                "format": voice_config.tts.format,
                "stream": voice_config.tts.stream,
                "sample_rate": voice_config.tts.sample_rate,
                "channels": voice_config.tts.channels,
                "sample_width": voice_config.tts.sample_width,
                "timeout": voice_config.tts.timeout,
                "output_dir": "tmp/",
                "delete_audio_file": True,
            },
            "local_play": {
                # src 的主链路不是在 StreamTTS 内部本地播放 PCM，而是把
                # AudioData chunk 交给连接层/前端播放器。控制台也采用同样
                # 的分层：StreamTTS 只负责生成 PCM，PCMStreamPlayer 负责播放。
                "enabled": False,
                "format": voice_config.tts.format,
            },
        }

    async def start(self) -> None:
        """启动参考 StreamTTS 消费任务。"""

        await self.pcm_player.start()
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._consume_stream_tts())

    async def feed_token(self, text: str) -> None:
        """把主智能体新增 token 放入参考 StreamTTS 的输入流。"""

        if text and not self._closed:
            # 参考 StreamTTS 的切段线程每收到一次 TEXT 消息只取一个片段。
            # 某些模型事件会把较长 delta 一次性吐出，所以这里仅做“流式颗粒度”
            # 拆分，不做任何清理/切句，让 base.py/utils.py 继续负责真正预处理。
            for start in range(0, len(text), MAX_STREAM_TEXT_CHUNK):
                await self._text_queue.put(text[start : start + MAX_STREAM_TEXT_CHUNK])

    async def flush(self) -> None:
        """结束本轮文本流并等待参考 StreamTTS 处理完剩余文本。"""

        if self._closed:
            return
        self._closed = True
        await self._text_queue.put(None)
        if self._worker_task:
            await self._worker_task
        await self.pcm_player.mark_segment_end()
        await self.pcm_player.drain()

    async def close(self) -> None:
        """关闭本轮 TTS 资源。"""

        await self.flush()
        close = getattr(self.tts_manager, "close", None)
        if callable(close):
            close()
        await self.pcm_player.close()

    async def _llm_text_stream(self) -> AsyncGenerator[str, None]:
        """把队列包装成参考 StreamTTS 接收的 LLM async generator。"""

        while True:
            item = await self._text_queue.get()
            try:
                if item is None:
                    return
                yield item
            finally:
                self._text_queue.task_done()

    async def _consume_stream_tts(self) -> None:
        """消费参考 StreamTTS 输出，并在每段文本对应音频生成时做情绪识别。"""

        async for audio_data, text in self.tts_manager.process_llm_stream(self._llm_text_stream()):
            if text:
                send_segment_text = getattr(self.pcm_player, "send_segment_text", None)
                if callable(send_segment_text):
                    await send_segment_text(text)
            # 对齐 src/back/connection_websocket.py：音频和文本都来自
            # StreamTTS。src 会把 audio_data.data 作为 base64 PCM chunk 发给
            # 前端；控制台则直接交给 Python 版 PCMStreamPlayer 播放。
            if audio_data and getattr(audio_data, "data", None):
                audio_format = getattr(audio_data, "format", "pcm")
                if audio_format == "pcm":
                    segment_start = bool(text)
                    await self.pcm_player.enqueue_pcm(
                        audio_data.data,
                        segment_start=segment_start,
                    )
                else:
                    print(
                        f"\n[voice] 收到非PCM音频格式 {audio_format}，"
                        "当前控制台流式播放器仅直接播放PCM。",
                        flush=True,
                    )
            if not text:
                continue
            emotion = self.emotion_extractor.extract(text)
            emotion_event = {"text": text, **emotion}
            self.spoken_segments.append(text)
            self.emotion_events.append(emotion_event)
            send_emotion = getattr(self.pcm_player, "send_emotion", None)
            if callable(send_emotion):
                await send_emotion(emotion_event)
            print(
                "\n[voice] 情绪: "
                f"{emotion.get('emotion_cn')} ({emotion.get('emotion_en')}) "
                f"{emotion.get('emoji')} | 文本: {text}",
                flush=True,
            )
