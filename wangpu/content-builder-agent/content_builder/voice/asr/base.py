"""ASR provider 的最小公共接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class ASRProviderBase(ABC):
    """语音识别 provider 基类。

    当前项目首版只接入 FunASR，但保留一个很薄的抽象层，方便以后增加麦克风
    PCM 数据识别或其他 provider，而不影响控制台入口。
    """

    @abstractmethod
    async def recognize_file(self, file_path: str | Path, session_id: str | None = None) -> str:
        """识别一个音频文件并返回文本。"""
