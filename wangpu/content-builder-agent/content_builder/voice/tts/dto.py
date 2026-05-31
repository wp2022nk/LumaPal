"""数据传输对象定义"""
from enum import Enum
from typing import Optional


class SentenceType(Enum):
    """句子类型"""
    FIRST = "FIRST"  # 首句话
    MIDDLE = "MIDDLE"  # 说话中
    LAST = "LAST"  # 最后一句


class ContentType(Enum):
    """内容类型"""
    TEXT = "TEXT"  # 文本内容
    FILE = "FILE"  # 文件内容
    ACTION = "ACTION"  # 动作内容


class InterfaceType(Enum):
    """接口类型"""
    DUAL_STREAM = "DUAL_STREAM"  # 双流式
    SINGLE_STREAM = "SINGLE_STREAM"  # 单流式
    NON_STREAM = "NON_STREAM"  # 非流式


class TTSMessageDTO:
    """TTS消息数据传输对象"""
    def __init__(
        self,
        sentence_id: str,
        sentence_type: SentenceType,
        content_type: ContentType,
        content_detail: Optional[str] = None,
        content_file: Optional[str] = None,
    ):
        self.sentence_id = sentence_id
        self.sentence_type = sentence_type
        self.content_type = content_type
        self.content_detail = content_detail
        self.content_file = content_file


class AudioData:
    """音频数据对象"""
    def __init__(self, data: bytes, format: str = "opus", sample_rate: int = 16000):
        self.data = data
        self.format = format
        self.sample_rate = sample_rate
