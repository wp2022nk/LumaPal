"""TTS基础类"""
import os
import re
import queue
import uuid
import asyncio
import threading
from datetime import datetime
from abc import ABC, abstractmethod
from typing import Optional, List, Tuple, Dict, Any
import logging

try:
    # 尝试相对导入（在包内使用时）
    from .dto import (
        TTSMessageDTO,
        SentenceType,
        ContentType,
        InterfaceType,
        AudioData
    )
    from .utils import (
        get_string_no_punctuation_or_emoji,
        MarkdownCleaner,
        filter_tool_call_info
    )
except ImportError:
    # 绝对导入（直接运行文件时）
    from dto import (
        TTSMessageDTO,
        SentenceType,
        ContentType,
        InterfaceType,
        AudioData
    )
    from utils import (
        get_string_no_punctuation_or_emoji,
        MarkdownCleaner,
        filter_tool_call_info
    )


class TTSProviderBase(ABC):
    """TTS提供商基类"""
    
    def __init__(self, config: Dict[str, Any]):
        """初始化TTS提供商
        
        Args:
            config: 配置字典，包含TTS相关设置
        """
        self.config = config
        self.interface_type = InterfaceType.NON_STREAM
        self.tts_timeout = config.get("timeout", 10)
        self.audio_file_type = config.get("format", "wav")
        self.output_dir = config.get("output_dir", "tmp/")
        self.delete_audio_file = config.get("delete_audio_file", True)
        
        # 队列和线程控制
        self.tts_text_queue = queue.Queue()
        self.tts_audio_queue = queue.Queue()
        self.stop_event = threading.Event()
        
        # 文本处理相关
        self.tts_text_buff = []  # 统一使用过滤后的文本
        self.processed_chars = 0
        self.is_first_sentence = True
        self.tts_stop_request = False
        
        # 标点符号配置
        self.punctuations = (
            "。", "？", "?", "！", "!", "；", ";", "：",
        )
        self.first_sentence_punctuations = (
            "，", "～", "~", "、", ",", "。", "？", "?", "！", "!", "；", ";", "：",
        )
        
        # 日志配置
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 确保输出目录存在
        os.makedirs(self.output_dir, exist_ok=True)

    def generate_filename(self, extension: str = ".wav") -> str:
        """生成唯一的音频文件名"""
        return os.path.join(
            self.output_dir,
            f"tts-{datetime.now().date()}@{uuid.uuid4().hex}{extension}",
        )


    @abstractmethod
    async def text_to_speak(self, text: str, output_file: Optional[str]) -> Optional[bytes]:
        """文本转语音的具体实现（需要子类实现）
        
        Args:
            text: 要转换的文本
            output_file: 输出文件路径（如果为None，则返回音频数据）
            
        Returns:
            如果output_file为None，返回音频字节数据；否则返回None
        """
        pass

    def start_processing(self):
        """启动处理线程"""
        if not hasattr(self, '_text_thread') or not self._text_thread.is_alive():
            self.stop_event.clear()
            self._text_thread = threading.Thread(
                target=self._text_processing_thread,
                daemon=True
            )
            self._text_thread.start()
            self.logger.info("TTS文本处理线程已启动")

    def stop_processing(self):
        """停止处理线程"""
        self.stop_event.set()
        if hasattr(self, '_text_thread'):
            self._text_thread.join(timeout=1)
        self.logger.info("TTS处理线程已停止")

    def put_text(self, message: TTSMessageDTO):
        """向文本队列添加消息"""
        self.tts_text_queue.put(message)

    def get_audio(self, timeout: float = 1.0) -> Optional[Tuple[SentenceType, Optional[AudioData], Optional[str]]]:
        """从音频队列获取音频数据
        
        Returns:
            元组(句子类型, 音频数据, 文本内容)
        """
        try:
            return self.tts_audio_queue.get(timeout=timeout)
        except queue.Empty:
            return None
    
    def clear_audio_queue(self):
        """
        清空音频队列和文本队列
        
        用于打断功能：丢弃所有已生成但未播放的音频，以及待处理的文本
        这样可以防止上一轮残留的文本在下一轮被合成
        """
        audio_cleared = 0
        text_cleared = 0
        
        # 清空音频队列
        while not self.tts_audio_queue.empty():
            try:
                self.tts_audio_queue.get_nowait()
                audio_cleared += 1
            except queue.Empty:
                break
        
        # 清空文本队列（防止残留文本在下一轮被处理）
        while not self.tts_text_queue.empty():
            try:
                self.tts_text_queue.get_nowait()
                text_cleared += 1
            except queue.Empty:
                break
        
        # 清空文本缓冲区
        if self.tts_text_buff:
            buff_cleared = len(self.tts_text_buff)
            self.tts_text_buff = []
            self.logger.info(f"🧹 [TTS Provider] 已清空文本缓冲区，丢弃 {buff_cleared} 个文本片段")
        
        if audio_cleared > 0 or text_cleared > 0:
            self.logger.info(f"🧹 [TTS Provider] 已清空队列: {audio_cleared} 个音频段, {text_cleared} 个待处理文本")
        
        return audio_cleared + text_cleared

    async def _process_tts_segment(self, sentence_type: SentenceType, segment_text: str):
        """处理TTS文本段，支持真正的流式输出
        
        Args:
            sentence_type: 句子类型
            segment_text: 要转换的文本段
        """
        # 先过滤工具调用信息，再清理markdown
        text = filter_tool_call_info(segment_text)
        text = MarkdownCleaner.clean_markdown(text)
        
        try:
            # 调用 text_to_speak
            result = await self.text_to_speak(text, None)
            
            # 检查是否是异步生成器（流式模式）
            if hasattr(result, '__aiter__'):
                # 流式模式的开始标记
                self.logger.info(f"\n{'='*60}")
                self.logger.info(f"🎵 [流式TTS] 开始合成: '{text}'")
                self.logger.info(f"{'='*60}")
                chunk_count = 0
                total_bytes = 0
                
                # 流式模式：每个chunk立即放入队列
                async for pcm_chunk in result:
                    chunk_count += 1
                    total_bytes += len(pcm_chunk)
                    
                    # 为每个PCM chunk创建AudioData
                    audio_data = AudioData(pcm_chunk, "pcm")
                    
                    # 立即放入队列，不等待后续chunk
                    self.tts_audio_queue.put(
                        (sentence_type, audio_data, text if chunk_count == 1 else "")
                    )
                    
                    # 显示chunk信息
                    self.logger.info(f"  ✅ Chunk #{chunk_count}: {len(pcm_chunk):,} bytes → 队列")
                
                # 流式模式的结束标记
                self.logger.info(f"{'='*60}")
                self.logger.info(f"✅ [流式TTS] 完成: 共 {chunk_count} chunks, {total_bytes:,} bytes")
                self.logger.info(f"{'='*60}\n")
                
            elif result:
                # 非流式模式：直接使用字节数据
                self.logger.info(f"🎵 [非流式TTS] '{text[:30]}...' → {len(result):,} bytes")
                audio_data = AudioData(result, self.audio_file_type)
                self.tts_audio_queue.put(
                    (sentence_type, audio_data, text)
                )
                
        except Exception as e:
            self.logger.error(f"❌ TTS生成失败: {e}")
    
    def _text_processing_thread(self):
        """文本处理线程"""
        self.logger.info("文本处理线程启动")
        
        while not self.stop_event.is_set():
            try:
                message = self.tts_text_queue.get(timeout=0.5)
                
                if message.sentence_type == SentenceType.FIRST:
                    # 初始化参数
                    self.tts_stop_request = False
                    self.processed_chars = 0
                    self.tts_text_buff = []
                    self.is_first_sentence = True
                    self.logger.info("开始新的TTS会话")
                    
                elif message.content_type == ContentType.TEXT:
                    # 统一使用过滤后的文本
                    filtered_text = filter_tool_call_info(message.content_detail)
                    
                    self.tts_text_buff.append(filtered_text)
                    segment_text = self._get_segment_text()
                    if segment_text:
                        self.logger.debug(f"处理文本片段: {segment_text}")
                        # 使用异步方法的同步包装，TTS和显示都用同样的过滤后文本
                        asyncio.run(self._process_tts_segment(message.sentence_type, segment_text))
                            
                elif message.content_type == ContentType.FILE:
                    # 处理剩余文本
                    self._process_remaining_text()
                    # 处理音频文件
                    if message.content_file and os.path.exists(message.content_file):
                        with open(message.content_file, "rb") as f:
                            audio_bytes = f.read()
                        audio_data = AudioData(audio_bytes, self.audio_file_type)
                        self.tts_audio_queue.put(
                            (message.sentence_type, audio_data, message.content_detail)
                        )

                if message.sentence_type == SentenceType.LAST:
                    # 处理剩余文本
                    has_remaining = self._process_remaining_text()
                    
                    # 无论是否有剩余文本，都需要发送空的LAST信号来标记结束
                    # 这样前端才能知道TTS已经完全结束
                    self.tts_audio_queue.put(
                        (message.sentence_type, None, None)
                    )
                    
                    if has_remaining:
                        self.logger.info("剩余文本已处理，已发送带内容的LAST信号 + 空的LAST结束信号")
                    else:
                        self.logger.info("无剩余文本，已发送空的LAST结束信号")
                    
                    self.logger.info("TTS会话结束")

            except queue.Empty:
                continue
            except Exception as e:
                self.logger.error(f"处理TTS文本失败: {e}", exc_info=True)

    def _get_segment_text(self) -> Optional[str]:
        """获取可以转换的文本段
        
        Returns:
            过滤后的文本段用于TTS和显示
        """
        # 合并当前全部文本并处理未分割部分
        full_text = "".join(self.tts_text_buff)
        current_text = full_text[self.processed_chars:]  # 从未处理的位置开始
        
        # 根据是否是第一句话选择不同的标点符号集合
        punctuations_to_use = (
            self.first_sentence_punctuations
            if self.is_first_sentence
            else self.punctuations
        )

        # 查找第一个标点符号（改为使用find而不是rfind，更快响应）
        first_punct_pos = -1
        for punct in punctuations_to_use:
            pos = current_text.find(punct)
            if pos != -1 and (first_punct_pos == -1 or pos < first_punct_pos):
                first_punct_pos = pos

        if first_punct_pos != -1:
            # 找到标点符号，立即处理这段文本
            segment_text_raw = current_text[:first_punct_pos + 1]
            
            # 对文本进行清理用于TTS和显示
            segment_text = get_string_no_punctuation_or_emoji(segment_text_raw)
            
            self.processed_chars += len(segment_text_raw)  # 更新处理位置

            # 如果是第一句话，在找到第一个逗号后，将标志设置为False
            if self.is_first_sentence:
                self.is_first_sentence = False

            # 返回过滤后的文本，确保与音频一致（不strip以保留空格）
            return segment_text
        elif self.tts_stop_request and current_text:
            # 如果请求停止且有剩余文本，返回所有剩余文本
            self.is_first_sentence = True  # 重置标志
            return current_text
        else:
            return None

    def _process_remaining_text(self):
        """处理剩余的文本（使用流式处理）
        
        Returns:
            bool: 是否有剩余文本被处理
        """
        full_text = "".join(self.tts_text_buff)
        remaining_text = full_text[self.processed_chars:]
        
        if remaining_text.strip():
            # 对剩余文本进行清理，用于TTS合成（不strip以保留空格）
            cleaned_text = get_string_no_punctuation_or_emoji(remaining_text)
            
            if cleaned_text:
                self.logger.info(f"🎵 处理剩余文本并合成音频: '{cleaned_text}'")
                
                # 使用流式处理剩余文本
                asyncio.run(self._process_tts_segment(SentenceType.LAST, cleaned_text))
                
                self.processed_chars = len(full_text)  # 标记为完全处理
                return True
            else:
                self.logger.info(f"剩余文本清理后为空，跳过处理")
        
        return False

    async def start_session(self, session_id: str):
        """开始TTS会话（可选实现）"""
        pass

    async def finish_session(self, session_id: str):
        """结束TTS会话（可选实现）"""
        pass

    async def close(self):
        """清理资源"""
        self.stop_processing()