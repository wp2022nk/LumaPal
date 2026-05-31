"""ASR 管理器。"""

from __future__ import annotations

from pathlib import Path

from ...config import VoiceASRConfig
from .base import ASRProviderBase
from .providers import FunASRProvider


class ASRManager:
    """统一封装控制台语音输入。

    目前 provider 固定为 funasr；外层只依赖 recognize_file，后续要增加麦克风
    录音时不需要改语音控制台主流程。
    """

    def __init__(self, config: VoiceASRConfig) -> None:
        self.config = config
        self.provider = self._create_provider(config)

    def _create_provider(self, config: VoiceASRConfig) -> ASRProviderBase:
        provider = config.provider.lower().strip()
        if provider != "funasr":
            raise ValueError(f"当前只支持本地 FunASR ASR provider，收到：{config.provider}")
        return FunASRProvider(config)

    async def recognize_file(self, file_path: str | Path, session_id: str | None = None) -> str:
        """识别音频文件并返回文本。"""

        return await self.provider.recognize_file(file_path, session_id=session_id)
