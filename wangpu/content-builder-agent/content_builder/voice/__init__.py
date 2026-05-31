"""控制台语音链路模块。

本包只负责 ASR-主智能体-TTS 的本地控制台编排：
- ASR 使用本地 FunASR 把音频文件转成文本；
- LLM 复用当前 content-builder Deep Agent；
- TTS 使用 Qwen TTS 只播报主智能体的正常回复；
- 情绪识别只作用于即将播报的回复文本段。
"""

from .console import interactive_voice_chat, run_voice_turn

__all__ = ["interactive_voice_chat", "run_voice_turn"]
