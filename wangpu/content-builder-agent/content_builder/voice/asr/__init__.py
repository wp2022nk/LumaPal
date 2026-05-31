"""本地 ASR 能力。

第一版只保留 FunASR 文件识别，避免把参考项目中与当前项目无关的云厂商
provider 一起带进来。
"""

from .manager import ASRManager

__all__ = ["ASRManager"]
