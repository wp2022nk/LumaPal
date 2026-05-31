"""FunASR 本地音频文件识别。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..base import ASRProviderBase
from ....config import VoiceASRConfig

logger = logging.getLogger(__name__)

try:
    from funasr import AutoModel
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    FUNASR_AVAILABLE = True
except ImportError:
    AutoModel = None
    FUNASR_AVAILABLE = False

    def rich_transcription_postprocess(text: str) -> str:
        return text


class FunASRProvider(ASRProviderBase):
    """基于本地 SenseVoiceSmall + FSMN-VAD 的 FunASR provider。"""

    def __init__(self, config: VoiceASRConfig) -> None:
        if not FUNASR_AVAILABLE:
            raise RuntimeError("缺少 funasr 依赖，请先在项目环境中安装 funasr。")

        self.config = config
        self.model_dir = Path(config.model_dir)
        self.vad_model_dir = Path(config.vad_model_dir)
        self.device = config.device

        if not self.model_dir.exists():
            raise FileNotFoundError(f"FunASR 模型目录不存在：{self.model_dir}")
        if not self.vad_model_dir.exists():
            raise FileNotFoundError(f"FunASR VAD 模型目录不存在：{self.vad_model_dir}")

        logger.info("正在加载 FunASR 模型：%s", self.model_dir)
        self.model = AutoModel(
            model=str(self.model_dir),
            vad_model=str(self.vad_model_dir),
            vad_kwargs={"max_single_segment_time": config.max_single_segment_time},
            disable_update=True,
            device=self.device,
        )
        logger.info("FunASR 模型加载完成")

    async def recognize_file(self, file_path: str | Path, session_id: str | None = None) -> str:
        """异步识别音频文件。

        FunASR 的 generate 是阻塞调用。这里把它放进默认线程池，避免控制台
        事件循环被 ASR 阻塞，也方便之后扩展麦克风录音。
        """

        audio_path = Path(file_path).expanduser().resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"音频文件不存在：{audio_path}")

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._recognize_file_sync, audio_path)

    def _recognize_file_sync(self, audio_path: Path) -> str:
        """同步执行 FunASR 识别并做富文本后处理。"""

        result = self.model.generate(
            input=str(audio_path),
            cache={},
            language="auto",
            use_itn=True,
            batch_size_s=60,
            merge_vad=True,
            merge_length_s=15,
        )
        if not result or "text" not in result[0]:
            return ""
        return rich_transcription_postprocess(result[0]["text"]).strip()
