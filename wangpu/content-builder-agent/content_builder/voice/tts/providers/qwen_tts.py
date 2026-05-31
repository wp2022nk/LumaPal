"""通义千问TTS提供商实现"""
import os
import base64
import struct
from typing import Optional, Dict, Any, AsyncGenerator, Union
import asyncio
import logging
import requests
import dashscope

try:
    # 尝试相对导入（在包内使用时）
    from ..base import TTSProviderBase
except ImportError:
    # 绝对导入（直接运行文件时）
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from base import TTSProviderBase


class QwenTTSProvider(TTSProviderBase):
    """通义千问TTS提供商"""
    
    def __init__(self, config: Dict[str, Any]):
        """初始化通义千问TTS
        
        Args:
            config: 配置字典，必须包含:
                - api_key: DashScope API密钥
                - model: 模型名称（默认 "qwen3-tts-flash"）
                - voice: 语音类型（默认 "Cherry"）
                - language_type: 语言类型（默认 "Chinese"）
                - base_url: API地址（默认北京地域）
                - format: 音频格式（默认 "wav"）
                - stream: 是否使用流式传输（默认 False）
                - sample_rate: 采样率（流式默认24000，非流式由服务器决定）
                - channels: 声道数（流式默认1）
                - sample_width: 采样位宽（流式默认2，即16位）
        """
        super().__init__(config)
        
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # API配置
        self.api_key = config.get("api_key")
        if not self.api_key:
            # 尝试从环境变量获取
            self.api_key = os.getenv("DASHSCOPE_API_KEY")
            if not self.api_key:
                raise ValueError("必须提供api_key或设置环境变量DASHSCOPE_API_KEY")
        
        # 设置API地址
        base_url = config.get("base_url", "https://dashscope.aliyuncs.com/api/v1")
        dashscope.base_http_api_url = base_url
        
        # 模型配置
        self.model = config.get("model", "qwen3-tts-flash")
        self.voice = config.get("voice", "Cherry")
        self.language_type = config.get("language_type", "Chinese")
        
        # 音频格式配置
        format_value = config.get("format", "wav")
        self.format = format_value if format_value else "wav"
        self.audio_file_type = self.format
        
        # 流式配置
        self.use_stream = config.get("stream", False)
        
        # 流式音频参数（PCM格式参数）
        # 根据官方文档，qwen3-tts-flash流式返回的是24kHz, 16bit, 单声道的PCM数据
        self.sample_rate = config.get("sample_rate", 24000)
        self.channels = config.get("channels", 1)
        self.sample_width = config.get("sample_width", 2)  # 2字节 = 16位
        
        self.logger.info(f"通义千问TTS初始化完成 - 模型: {self.model}, 音色: {self.voice}, 流式: {self.use_stream}")

    @staticmethod
    def _create_wav_header(pcm_data_size: int, sample_rate: int = 24000, 
                          channels: int = 1, sample_width: int = 2) -> bytes:
        """创建WAV文件头
        
        Args:
            pcm_data_size: PCM数据大小（字节）
            sample_rate: 采样率（Hz）
            channels: 声道数
            sample_width: 采样位宽（字节）
            
        Returns:
            WAV文件头（44字节）
        """
        # RIFF头
        riff_header = b'RIFF'
        file_size = pcm_data_size + 36  # 文件总大小 - 8
        riff_size = struct.pack('<I', file_size)
        wave_header = b'WAVE'
        
        # fmt子块
        fmt_header = b'fmt '
        fmt_size = struct.pack('<I', 16)  # fmt块大小（固定16字节）
        audio_format = struct.pack('<H', 1)  # PCM格式
        num_channels = struct.pack('<H', channels)
        sample_rate_bytes = struct.pack('<I', sample_rate)
        byte_rate = struct.pack('<I', sample_rate * channels * sample_width)
        block_align = struct.pack('<H', channels * sample_width)
        bits_per_sample = struct.pack('<H', sample_width * 8)
        
        # data子块头
        data_header = b'data'
        data_size = struct.pack('<I', pcm_data_size)
        
        # 组合WAV头（44字节）
        wav_header = (
            riff_header + riff_size + wave_header +
            fmt_header + fmt_size + audio_format + num_channels +
            sample_rate_bytes + byte_rate + block_align + bits_per_sample +
            data_header + data_size
        )
        
        return wav_header

    async def text_to_speak(self, text: str, output_file: Optional[str] = None) -> Union[Optional[bytes], AsyncGenerator[bytes, None]]:
        """使用通义千问TTS将文本转换为语音
        
        根据 use_stream 配置自动选择流式或非流式模式
        
        Args:
            text: 要转换的文本
            output_file: 输出文件路径（流式模式下不支持，请使用非流式模式）
            
        Returns:
            - 非流式模式: 返回完整的音频字节数据（如果output_file为None）或None（已保存到文件）
            - 流式模式: 返回异步生成器，每次yield一个PCM音频块
            
        Example:
            # 非流式
            audio_bytes = await provider.text_to_speak("你好")
            
            # 流式
            async for pcm_chunk in await provider.text_to_speak("你好"):
                await send_to_frontend(pcm_chunk)
        """
        try:
            # 根据配置选择流式或非流式
            if self.use_stream:
                # 流式模式：直接返回异步生成器（真正的流式）
                if output_file:
                    raise ValueError("流式模式不支持output_file参数，请使用非流式模式或使用stream_tts()方法")
                return self._stream_tts(text)
            else:
                # 非流式调用
                return await self._non_stream_tts(text, output_file)
        except Exception as e:
            raise Exception(f"通义千问TTS请求失败: {e}")
    
    async def stream_tts(self, text: str):
        """流式TTS - 真正的流式生成器，每个chunk立即yield
        
        这是推荐的流式调用方式，每收到一个音频chunk就立即yield出去
        
        Args:
            text: 要转换的文本
            
        Yields:
            bytes: PCM音频数据块（24kHz, 16bit, 单声道）
            
        Example:
            async for pcm_chunk in provider.stream_tts("你好世界"):
                await send_to_frontend(pcm_chunk)
        """
        async for chunk in self._stream_tts(text):
            yield chunk
    

    async def _non_stream_tts(self, text: str, output_file: Optional[str]) -> Optional[bytes]:
        """非流式TTS调用"""
        try:
            # 在同步环境中调用dashscope API
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: dashscope.MultiModalConversation.call(
                    model=self.model,
                    api_key=self.api_key,
                    text=text,
                    voice=self.voice,
                    language_type=self.language_type,
                    stream=False
                )
            )
            
            # 提取音频数据
            if response.output and hasattr(response.output, 'audio'):
                audio = response.output.audio
                audio_bytes = None
                
                # 优先使用base64数据
                if hasattr(audio, 'data') and audio.data:
                    # 解码base64音频数据
                    audio_bytes = base64.b64decode(audio.data)
                # 如果没有data,尝试从URL下载
                elif hasattr(audio, 'url') and audio.url:
                    self.logger.info(f"从URL下载音频: {audio.url}")
                    
                    def download_audio():
                        resp = requests.get(audio.url, timeout=30)
                        resp.raise_for_status()
                        return resp.content
                    
                    audio_bytes = await asyncio.get_event_loop().run_in_executor(
                        None, download_audio
                    )
                
                if audio_bytes:
                    if output_file:
                        # 确保目录存在
                        output_dir = os.path.dirname(output_file)
                        if output_dir:
                            os.makedirs(output_dir, exist_ok=True)
                        
                        # 保存到文件
                        with open(output_file, "wb") as f:
                            f.write(audio_bytes)
                        return None
                    else:
                        # 返回音频二进制数据
                        return audio_bytes
            
            raise Exception(f"通义千问TTS响应中没有音频数据或URL: {response}")
            
        except Exception as e:
            raise Exception(f"非流式TTS调用失败: {e}")

    async def _stream_tts(self, text: str):
        """流式TTS调用 - 真正的流式生成器，每个chunk立即yield
        
        Args:
            text: 要转换的文本
            
        Yields:
            bytes: PCM音频数据块（24kHz, 16bit, 单声道）
        """
        async for chunk in self._stream_tts_impl(text):
            yield chunk

    async def _stream_tts_impl(self, text: str):
        """真正的流式TTS实现 - 异步生成器，边合成边yield
        
        这是真正的流式实现，每收到一个音频chunk就立即yield出去
        不会等待整句话合成完毕
        
        Args:
            text: 要转换的文本
            
        Yields:
            bytes: PCM音频数据块（24kHz, 16bit, 单声道）
        """
        try:
            self.logger.info(f"开始流式TTS合成: {text[:50]}...")
            
            chunk_count = 0
            total_bytes = 0
            
            # 注意：DashScope的流式response是“同步迭代器”（官方示例也是 for chunk in response）
            # 在本项目中，TTS通常运行在TTS处理线程内（见 TTSProviderBase._text_processing_thread 里的 asyncio.run），
            # 所以这里直接同步迭代不会阻塞主事件循环。
            response = dashscope.MultiModalConversation.call(
                api_key=self.api_key,
                model=self.model,
                text=text,
                voice=self.voice,
                language_type=self.language_type,
                stream=True,  # 关键：启用流式
            )

            last_chunk = None
            for chunk in response:
                last_chunk = chunk

                # 如果SDK以chunk形式返回错误信息，这里要显式抛出，避免“悄悄没有音频”
                status_code = getattr(chunk, "status_code", None)
                if status_code is not None and status_code != 200:
                    code = getattr(chunk, "code", None)
                    message = getattr(chunk, "message", None)
                    raise Exception(
                        f"DashScope流式返回错误: status_code={status_code}, code={code}, message={message}"
                    )

                output = getattr(chunk, "output", None)
                if output is None:
                    continue

                audio = getattr(output, "audio", None)
                if audio is None:
                    continue

                data = getattr(audio, "data", None)
                if data:
                    pcm_bytes = base64.b64decode(data)
                    if pcm_bytes:
                        chunk_count += 1
                        total_bytes += len(pcm_bytes)
                        self.logger.debug(f"Yield chunk {chunk_count}: {len(pcm_bytes)} 字节")
                        yield pcm_bytes

                finish_reason = getattr(output, "finish_reason", None)
                if finish_reason == "stop":
                    break

            if chunk_count == 0:
                status_code = getattr(last_chunk, "status_code", None) if last_chunk else None
                code = getattr(last_chunk, "code", None) if last_chunk else None
                message = getattr(last_chunk, "message", None) if last_chunk else None
                raise Exception(
                    f"流式TTS未接收到任何音频数据(last_chunk_status_code={status_code}, code={code}, message={message})"
                )

            self.logger.info(f"流式TTS完成: {chunk_count} chunks, {total_bytes} 字节")
                
        except Exception as e:
            self.logger.error(f"流式TTS生成失败: {e}")
            raise


if __name__ == "__main__":
    async def main():
        api_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")
        if not api_key:
            raise RuntimeError(
                "缺少 Qwen TTS API key。请先设置 DASHSCOPE_API_KEY，"
                "或设置 QWEN_API_KEY 供本地示例复用。"
            )

        # 测试配置（流式）
        print("\n" + "=" * 50)
        print("测试2: 流式调用")
        print("=" * 50)
        
        config_stream = {
            "api_key": api_key,
            "model": "qwen3-tts-flash",
            "voice": "Cherry",
            "language_type": "Chinese",
            "format": "wav",
            "stream": True,
        }
        
        try:
            provider = QwenTTSProvider(config_stream)
            await provider.text_to_speak("这是流式合成测试，语音合成效果很好。", "test_qwen_stream.wav")
            print("✓ 测试完成！音频已保存到 test_qwen_stream.wav")
        except Exception as e:
            print(f"✗ 测试失败: {e}")
    
    asyncio.run(main())
