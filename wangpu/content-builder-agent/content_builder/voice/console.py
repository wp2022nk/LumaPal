"""ASR-主智能体-TTS 的控制台入口实现。"""

from __future__ import annotations

import asyncio
import math
import struct
from pathlib import Path

from ..agent_factory import create_content_writer
from ..config import DEFAULT_THREAD_ID, MainAgentConfig, load_main_config
from ..streaming import ConsoleStreamPrinter, astream_agent_events, configure_console_encoding
from .asr import ASRManager
from .tts import VoiceResponseSpeaker
from .tts.pcm_player import PCMPlaybackConfig, PCMStreamPlayer


class MainTokenDeltaFilter:
    """把主智能体 token 规整成真正增量。

    部分 LangGraph/模型组合会把 ``text-delta`` 作为“截至当前的累计文本”发出，
    而不是严格的新增 token。控制台显示可以容忍这种差异，但 TTS 如果直接消费
    累计文本，就会反复把整段故事送去合成。这里在语音链路前做一次增量去重。
    """

    def __init__(self) -> None:
        self._seen_text = ""

    def delta(self, text: str) -> str:
        """返回相对于历史主智能体文本的新内容。"""

        if not text:
            return ""

        # 累计文本形态：第二次收到“你好世界”，此前已经见过“你好”，只取“世界”。
        if self._seen_text and text.startswith(self._seen_text):
            new_text = text[len(self._seen_text) :]
            self._seen_text = text
            return new_text

        # 完全重复或短回放，直接丢弃。
        if self._seen_text.endswith(text):
            return ""

        # 处理轻微重叠：历史以“故事”结尾，新片段以“故事继续”开头，只取“继续”。
        max_overlap = min(len(self._seen_text), len(text))
        for overlap in range(max_overlap, 0, -1):
            if self._seen_text.endswith(text[:overlap]):
                new_text = text[overlap:]
                self._seen_text += new_text
                return new_text

        # 正常 token delta 形态：直接追加。
        self._seen_text += text
        return text


def normalize_voice_text(text: str) -> str:
    """归一化文本，用于判断事件流里是否回放了本轮用户输入。"""

    return "".join(str(text or "").split())


class UserEchoFilter:
    """过滤 LangGraph 事件流中偶发的用户输入回放。

    部分 v3 messages 投影会把本轮 human 输入也作为 main text-delta 发出来。
    普通控制台直接显示时只是多刷一行，但语音链路如果喂给 TTS，就会把用户
    自己的问题也播出来。这里仅过滤和本轮用户输入重合的片段。
    """

    def __init__(self, user_message: str) -> None:
        self._normalized_user_message = normalize_voice_text(user_message)

    def is_user_echo(self, text: str) -> bool:
        """判断一段文本是否是本轮用户输入的回放。"""

        normalized = normalize_voice_text(text)
        if not normalized or not self._normalized_user_message:
            return False
        return normalized in self._normalized_user_message


def ensure_voice_ready(config: MainAgentConfig) -> None:
    """在语音入口启动前检查必要的本地密钥。"""

    if not config.secrets.qwen_api_key:
        raise RuntimeError(
            "缺少主智能体 Qwen API key。请复制 secrets.example.yaml 为 "
            "secrets.local.yaml，并填写 qwen.api_key。"
        )
    if not (config.secrets.dashscope_api_key or config.secrets.qwen_api_key):
        raise RuntimeError(
            "缺少 Qwen TTS API key。请在 secrets.local.yaml 中填写 "
            "dashscope.api_key，或填写 qwen.api_key 供 TTS 复用。"
        )


async def play_voice_test_tone(config: MainAgentConfig) -> None:
    """播放一段本地 PCM 测试音。

    这个诊断命令绕过 ASR、LLM 和 Qwen TTS，只测试控制台 PCM 播放链路。
    如果这里能听到声音，说明本机播放器可用；如果听不到，优先排查系统输出
    设备、sounddevice 安装情况或 Windows 音量设置。
    """

    sample_rate = config.voice.tts.sample_rate
    duration_seconds = 0.45
    frequency = 660
    amplitude = 0.25
    sample_count = int(sample_rate * duration_seconds)
    pcm = bytearray()

    for index in range(sample_count):
        value = int(32767 * amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
        pcm.extend(struct.pack("<h", value))

    player = PCMStreamPlayer(
        PCMPlaybackConfig(
            sample_rate=sample_rate,
            channels=config.voice.tts.channels,
            sample_width=config.voice.tts.sample_width,
        ),
        enabled=config.voice.playback.enabled,
    )
    await player.start()
    await player.enqueue_pcm(bytes(pcm))
    await player.close()


async def run_voice_turn(
    message: str,
    *,
    agent=None,
    config: MainAgentConfig | None = None,
    thread_id: str = DEFAULT_THREAD_ID,
    images: list[str] | None = None,
) -> None:
    """运行一轮异步语音对话。

    这里是核心编排：异步消费 Deep Agent 事件流；控制台照常显示全部事件；
    只有主智能体 token/final 会进入 VoiceResponseSpeaker 做切段、情绪识别和 TTS。
    """

    runtime_config = config or load_main_config()
    ensure_voice_ready(runtime_config)
    active_agent = agent or create_content_writer(runtime_mode="cli")
    printer = ConsoleStreamPrinter()
    speaker = VoiceResponseSpeaker(runtime_config.voice, runtime_config.secrets)
    await speaker.start()

    saw_main_token = False
    main_delta_filter = MainTokenDeltaFilter()
    user_echo_filter = UserEchoFilter(message)
    try:
        async for event in astream_agent_events(
            active_agent,
            message,
            thread_id=thread_id,
            max_turns=runtime_config.conversation.max_turns,
            images=images,
        ):
            if event.type == "token" and event.source == "main":
                delta_text = main_delta_filter.delta(event.text)
                if delta_text and not user_echo_filter.is_user_echo(delta_text):
                    saw_main_token = True
                    await speaker.feed_token(delta_text)
                # 语音模式下，主智能体正文只在 TTS 切成可播报段后显示，
                # 避免“先刷完整文本、再慢慢合成音频”的观感。工具调用、
                # 任务规划、工具结果等非正文事件仍交给打印器显示。
                continue
            elif event.type == "final" and event.source == "main":
                if saw_main_token:
                    # token 流已经播过主体内容，final 只负责冲刷尾段，避免重复播报完整回答。
                    await speaker.flush()
                elif event.text and not user_echo_filter.is_user_echo(event.text):
                    # 某些模型或测试桩不会流式吐 token，只在 final 给出最终文本。
                    await speaker.feed_token(event.text)
                    await speaker.flush()
                continue

            printer.print(event)
    finally:
        printer.finish()
        await speaker.flush()
        await speaker.close()


async def interactive_voice_chat(
    config_path: str | Path | None = None,
    thread_id: str = DEFAULT_THREAD_ID,
) -> None:
    """启动控制台多轮语音交互。

    普通文本会直接发送给主智能体；/voice <path> 会先用 FunASR 识别音频文件，
    再把识别文本作为本轮用户输入。图片命令沿用原控制台的“待发送图片队列”。
    """

    configure_console_encoding()
    runtime_config = load_main_config(config_path)
    ensure_voice_ready(runtime_config)
    agent = create_content_writer(config_path, runtime_mode="cli")
    resolved_thread_id = thread_id or runtime_config.thread_id or DEFAULT_THREAD_ID
    pending_images: list[str] = []
    asr_manager: ASRManager | None = None

    print("Content Builder Voice Agent 已启动。输入 exit 或 quit 退出。")
    print(f"当前 thread_id: {resolved_thread_id}")
    print("语音输入：/voice 音频文件路径")
    print("播放测试：/voice-test 直接播放一段本地PCM测试音")
    print("图片输入：/image 图片路径 或 /image 图片URL；下一条普通消息会自动带上图片。")

    while True:
        try:
            raw_message = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return

        if not raw_message:
            continue
        command = raw_message.lower()
        if command in {"exit", "quit"}:
            print("已退出。")
            return

        if command.startswith("/image "):
            image = raw_message[len("/image ") :].strip().strip('"').strip("'")
            if not image:
                print("用法：/image 图片路径 或 /image 图片URL")
                continue
            pending_images.append(image)
            print(f"已添加图片，将随下一条消息发送：{image}")
            continue

        if command == "/images":
            if not pending_images:
                print("当前没有待发送图片。")
                continue
            print("待发送图片：")
            for index, image in enumerate(pending_images, start=1):
                print(f"  {index}. {image}")
            continue

        if command == "/clear-images":
            pending_images.clear()
            print("已清空待发送图片。")
            continue

        if command == "/voice-test":
            print("[voice] 正在播放本地PCM测试音。")
            await play_voice_test_tone(runtime_config)
            continue

        message = raw_message
        if command.startswith("/voice "):
            audio_path = raw_message[len("/voice ") :].strip().strip('"').strip("'")
            if not audio_path:
                print("用法：/voice 音频文件路径")
                continue
            try:
                if asr_manager is None:
                    print("[voice] 正在初始化本地 FunASR，首次加载可能需要一些时间。")
                    asr_manager = ASRManager(runtime_config.voice.asr)
                message = await asr_manager.recognize_file(audio_path)
            except Exception as exc:
                print(f"[voice] 语音识别失败：{exc}")
                continue
            if not message:
                print("[voice] 没有识别到有效文本。")
                continue
            print(f"[voice] 识别文本：{message}")

        images_for_turn = list(pending_images)
        try:
            await run_voice_turn(
                message,
                agent=agent,
                config=runtime_config,
                thread_id=resolved_thread_id,
                images=images_for_turn,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"\n输入错误：{exc}")
            continue
        except RuntimeError as exc:
            print(f"\n运行错误：{exc}")
            continue

        if images_for_turn:
            pending_images.clear()


def run_interactive_voice_chat() -> None:
    """同步包装，供脚本入口直接调用。"""

    asyncio.run(interactive_voice_chat())
