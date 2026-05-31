from __future__ import annotations

import asyncio
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

from content_builder.config import (
    SecretsConfig,
    VoiceASRConfig,
    VoiceConfig,
    VoicePlaybackConfig,
    VoiceTTSConfig,
)
from content_builder.streaming import astream_agent_events
from content_builder.voice.console import UserEchoFilter
from content_builder.voice.emotion import EmotionExtractor
from content_builder.voice.tts.base import TTSProviderBase
from content_builder.voice.tts.dto import AudioData, ContentType, SentenceType, TTSMessageDTO
from content_builder.voice.tts.pcm_player import PCMStreamPlayer
from content_builder.voice.tts.speaker import VoiceResponseSpeaker


class VoiceStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_astream_agent_events_normalizes_main_tokens(self) -> None:
        class FakeAgent:
            async def astream_events(self, *_args: Any, **_kwargs: Any):
                yield {
                    "method": "messages",
                    "params": {
                        "namespace": [],
                        "data": [
                            {
                                "event": "content-block-delta",
                                "delta": {"type": "text-delta", "text": "hello."},
                            }
                        ],
                    },
                }

        events = [
            event
            async for event in astream_agent_events(FakeAgent(), "test", thread_id="voice-test")
        ]

        self.assertEqual([event.type for event in events], ["token", "final"])
        self.assertEqual(events[0].source, "main")
        self.assertEqual(events[-1].text, "hello.")

    async def test_astream_agent_events_accepts_coroutine_returning_async_iterator(self) -> None:
        class FakeAgent:
            def astream_events(self, *_args: Any, **_kwargs: Any):
                async def make_stream():
                    async def stream():
                        yield {
                            "method": "messages",
                            "params": {
                                "namespace": [],
                                "data": [
                                    {
                                        "event": "content-block-delta",
                                        "delta": {"type": "text-delta", "text": "ok."},
                                    }
                                ],
                            },
                        }

                    return stream()

                return make_stream()

        events = [
            event
            async for event in astream_agent_events(FakeAgent(), "test", thread_id="voice-test")
        ]

        self.assertEqual([event.type for event in events], ["token", "final"])
        self.assertEqual(events[-1].text, "ok.")

    async def test_voice_speaker_uses_stream_tts_output_for_emotion(self) -> None:
        class FakeStreamTTS:
            def __init__(self) -> None:
                self.received: list[str] = []
                self.closed = False

            async def process_llm_stream(self, llm_stream, session_id=None, on_audio=None):
                async for text in llm_stream:
                    self.received.append(text)
                    yield AudioData(b"\x00\x00\x01\x00", "pcm", 24000), text

            def close(self) -> None:
                self.closed = True

        class FakePCMPlayer:
            def __init__(self) -> None:
                self.started = False
                self.closed = False
                self.chunks: list[bytes] = []

            async def start(self) -> None:
                self.started = True

            async def enqueue_pcm(self, pcm_data: bytes, *, segment_start: bool = False) -> None:
                self.chunks.append(pcm_data)

            async def mark_segment_end(self) -> None:
                return None

            async def drain(self) -> None:
                return None

            async def close(self) -> None:
                self.closed = True

        voice_config = make_voice_config()
        fake_tts = FakeStreamTTS()
        fake_pcm_player = FakePCMPlayer()
        speaker = VoiceResponseSpeaker(
            voice_config,
            SecretsConfig(path=Path("secrets.local.yaml"), qwen_api_key="test"),
            tts_manager=fake_tts,
            emotion_extractor=EmotionExtractor(),
            pcm_player=fake_pcm_player,
        )

        stdout = StringIO()
        with redirect_stdout(stdout):
            await speaker.start()
            await speaker.feed_token("好的，资料整理完成了。")
            await speaker.flush()
            await speaker.close()

        self.assertEqual("".join(fake_tts.received), "好的，资料整理完成了。")
        self.assertGreaterEqual(len(fake_tts.received), 1)
        self.assertTrue(fake_tts.closed)
        self.assertTrue(fake_pcm_player.started)
        self.assertTrue(fake_pcm_player.closed)
        self.assertEqual(fake_pcm_player.chunks, [b"\x00\x00\x01\x00"])
        self.assertEqual(len(speaker.emotion_events), 1)
        self.assertEqual(speaker.emotion_events[0]["text"], "好的，资料整理完成了。")
        printed = stdout.getvalue()
        self.assertIn("[voice] 情绪:", printed)
        self.assertIn("文本: 好的，资料整理完成了。", printed)
        self.assertNotIn("[voice:tts]", printed)

    async def test_user_echo_filter_ignores_current_user_message(self) -> None:
        echo_filter = UserEchoFilter("为什么程序员总是分不清万圣节和圣诞节？")

        self.assertTrue(echo_filter.is_user_echo("为什么程序员总是分不清万圣节和圣诞节？"))
        self.assertTrue(echo_filter.is_user_echo("为什么程序员总是分不清"))
        self.assertFalse(echo_filter.is_user_echo("因为 Oct 31 == Dec 25。"))

    async def test_pcm_player_close_returns_after_sounddevice_worker_stops(self) -> None:
        class FakePCMStreamPlayer(PCMStreamPlayer):
            def __init__(self) -> None:
                self.written: list[bytes] = []
                super().__init__(enabled=True)

            def _init_output_stream(self) -> None:
                self._player_type = "sounddevice"
                self._available = True
                self._stream = object()

            def _write_chunk(self, chunk: bytes) -> None:
                self.written.append(chunk)

        player = FakePCMStreamPlayer()
        await player.start()
        await player.enqueue_pcm(b"\x00" * 12000, segment_start=True)
        await asyncio.wait_for(player.close(), timeout=1)

        self.assertEqual(player.written, [b"\x00" * 12000])


class CopiedTTSPreprocessTests(unittest.TestCase):
    def test_reference_tts_base_segments_long_markdown_story(self) -> None:
        class RecordingProvider(TTSProviderBase):
            def __init__(self) -> None:
                super().__init__({"format": "wav", "output_dir": "tmp/"})
                self.texts: list[str] = []

            async def text_to_speak(self, text: str, output_file=None):
                self.texts.append(text)
                return b"RIFFfake"

        provider = RecordingProvider()
        provider.start_processing()
        try:
            session_id = "test"
            provider.put_text(TTSMessageDTO(session_id, SentenceType.FIRST, ContentType.ACTION))
            story = (
                "好的，给你讲一个温暖的小故事：\n---\n\n**《星星和萤火虫》**\n\n"
                "从前，有一只小萤火虫，名叫闪闪。\n\n"
                "闪闪是森林里最小的一只萤火虫，他发出的光也很微弱，只能照亮一片树叶。"
            )
            for start in range(0, len(story), 12):
                provider.put_text(
                    TTSMessageDTO(
                        session_id,
                        SentenceType.MIDDLE,
                        ContentType.TEXT,
                        story[start : start + 12],
                    )
                )
            provider.put_text(TTSMessageDTO(session_id, SentenceType.LAST, ContentType.ACTION))

            # Drain until the empty LAST signal arrives.
            while True:
                result = provider.get_audio(timeout=3)
                self.assertIsNotNone(result)
                sentence_type, audio_data, text = result
                if sentence_type == SentenceType.LAST and audio_data is None and text is None:
                    break
        finally:
            provider.stop_processing()

        self.assertGreaterEqual(len(provider.texts), 4)
        self.assertIn("好的，", provider.texts[0])
        self.assertTrue(all("---" not in text for text in provider.texts))
        self.assertTrue(all("**" not in text for text in provider.texts))
        self.assertTrue(all(len(text) < 80 for text in provider.texts))


def make_voice_config() -> VoiceConfig:
    return VoiceConfig(
        enabled=True,
        asr=VoiceASRConfig(
            provider="funasr",
            model_dir=Path("model"),
            vad_model_dir=Path("vad"),
        ),
        tts=VoiceTTSConfig(
            provider="qwen",
            model="qwen3-tts-flash",
            voice="Cherry",
            language_type="Chinese",
            format="wav",
            stream=True,
            sample_rate=24000,
            channels=1,
            sample_width=2,
            timeout=30,
        ),
        playback=VoicePlaybackConfig(enabled=True),
    )


if __name__ == "__main__":
    unittest.main()
