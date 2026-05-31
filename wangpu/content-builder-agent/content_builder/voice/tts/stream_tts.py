"""流式TTS主接口"""
import asyncio
import uuid
import logging
from typing import Optional, AsyncGenerator, Dict, Any, Callable
from concurrent.futures import ThreadPoolExecutor

# 导入中断管理器
try:
    from utils.interrupt_manager import get_interrupt_manager
except:
    # 如果导入失败，使用空实现（兼容性处理）
    class DummyInterruptManager:
        def is_interrupted(self, session_id=None):
            return False
    def get_interrupt_manager():
        return DummyInterruptManager()

try:
    # 尝试相对导入（在包内使用时）
    from .dto import (
        TTSMessageDTO,
        SentenceType,
        ContentType,
        AudioData
    )
    from .base import TTSProviderBase
    from .providers.qwen_tts import QwenTTSProvider
    from .audio_player import play_audio
    from .utils import filter_tool_call_info
except ImportError:
    # 绝对导入（直接运行文件时）
    from dto import (
        TTSMessageDTO,
        SentenceType,
        ContentType,
        AudioData
    )
    from base import TTSProviderBase
    from providers.qwen_tts import QwenTTSProvider
    from audio_player import play_audio
    from utils import filter_tool_call_info

# 注册的TTS提供商
PROVIDERS = {
    "qwen": QwenTTSProvider,
}


class StreamTTS:
    """流式TTS接口
    
    提供统一的接口来处理LLM流式文本输入，并返回流式音频输出。
    """
    
    def __init__(self, config : dict = None):
        """初始化流式TTS
        
        Args:
            config: 配置字典，如果不提供则使用默认配置
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # 加载配置
        if config:
            self.config = config
        else:
            # 默认配置
            self.config = {
                "provider": "qwen",
                "qwen": {
                    "voice": "Cherry",
                    "model": "qwen3-tts-flash",
                    "language_type": "Chinese",
                    "format": "wav",
                    "stream": False,
                    "timeout": 10,
                    "delete_audio_file": True,
                    "output_dir": "tmp/"
                },
                # 本地播放配置（默认关闭）
                "local_play": {
                    "enabled": True,  # 设为True启用本地播放
                    "format": "mp3"
                }
            }
        
        self.provider = self.handel_config(self.config)

        
        # 线程池用于异步执行
        self.executor = ThreadPoolExecutor(max_workers=2)


    def handel_config(self,config:dict):
        """
        处理配置
        """
        # 创建TTS提供商实例
        provider_name = config.get("provider", "edge")
        if provider_name not in PROVIDERS:
            raise ValueError(f"不支持的TTS提供商: {provider_name}")
            
        provider_class = PROVIDERS[provider_name]
        provider_config = config.get(provider_name, {})
        provider = provider_class(provider_config)
        return provider
        
    def change_config(self,config:dict):
        """
        更换配置
        """
        self.config = config
        self.provider = self.handel_config(self.config)



    async def process_llm_stream(
        self,
        llm_stream: AsyncGenerator[str, None],
        session_id: Optional[str] = None,
        on_audio: Optional[Callable[[AudioData, str], None]] = None
    ) -> AsyncGenerator[tuple[AudioData, str], None]:
        """
        处理LLM流式输出并返回音频流
        
        关键功能：
        1. 接收LLM流式文本，转换为TTS音频
        2. 支持打断功能，检测到打断信号时立即停止
        3. 打断时清空TTS内部队列，不再生成新音频
        
        Args:
            llm_stream: LLM的异步文本生成器
            session_id: 会话ID（可选），用于支持打断功能
            on_audio: 音频回调函数（可选）
            
        Yields:
            (AudioData, text) 元组
        """
        if not session_id:
            session_id = str(uuid.uuid4())
        
        # 获取中断管理器
        interrupt_manager = get_interrupt_manager()
        
        # 启动TTS处理
        self.provider.start_processing()
        
        try:
            # 发送开始信号
            start_message = TTSMessageDTO(
                sentence_id=session_id,
                sentence_type=SentenceType.FIRST,
                content_type=ContentType.ACTION
            )
            self.provider.put_text(start_message)
            
            # 创建任务来处理LLM流和音频输出
            async def feed_llm_stream():
                """
                将LLM流送入TTS
                
                打断处理：检测到打断时立即停止接收LLM文本
                """
                try:
                    async for text_chunk in llm_stream:
                        # 🔍 检查是否触发打断
                        if interrupt_manager.is_interrupted(session_id):
                            self.logger.info(f"🛑 [TTS] 检测到打断信号 (会话: {session_id})，停止接收LLM文本")
                            break
                        
                        if text_chunk and text_chunk.strip():
                            # 发送原始文本到TTS处理（TTS内部会进行过滤）
                            text_message = TTSMessageDTO(
                                sentence_id=session_id,
                                sentence_type=SentenceType.MIDDLE,
                                content_type=ContentType.TEXT,
                                content_detail=text_chunk
                            )
                            self.provider.put_text(text_message)
                
                except Exception as e:
                    self.logger.error(f"❌ [TTS] 处理LLM流时出现错误: {e}")
                finally:
                    # 无论是正常结束还是被打断，都发送结束信号让TTS正常结束
                    end_message = TTSMessageDTO(
                        sentence_id=session_id,
                        sentence_type=SentenceType.LAST,
                        content_type=ContentType.ACTION
                    )
                    self.provider.put_text(end_message)
            
            async def get_audio_stream():
                """
                从TTS获取音频流
                
                打断处理：检测到打断时立即结束，清空所有队列
                """
                while True:
                    # 🔍 检查是否触发打断
                    if interrupt_manager.is_interrupted(session_id):
                        self.logger.info(f"🛑 [TTS] 检测到打断信号 (会话: {session_id})，立即终止")
                        break
                    
                    result = await asyncio.get_event_loop().run_in_executor(
                        self.executor,
                        self.provider.get_audio,
                        1.0
                    )
                    
                    if result:
                        sentence_type, audio_data, text = result
                        
                        # 调试日志
                        has_audio = audio_data is not None
                        has_text = text is not None and text != ""
                        self.logger.debug(f"📦 从队列取出: type={sentence_type.name}, audio={has_audio}, text='{text[:20] if text else '(empty)'}'")
                        
                        # 🔍 再次检查打断（在发送音频前）
                        if interrupt_manager.is_interrupted(session_id):
                            self.logger.info(f"🛑 [TTS] 检测到打断信号，丢弃当前音频段")
                            break
                        
                        # 只要有音频数据或文本就处理（支持流式TTS的多个chunk）
                        if audio_data or text:
                            if audio_data:
                                # 有音频数据时的正常处理
                                # 本地播放音频（如果启用）
                                local_play_config = self.config.get("local_play", {})
                                if local_play_config.get("enabled", False):
                                    # 直接调用播放（内部会加入队列）
                                    play_audio(
                                        audio_data.data,
                                        audio_data.format,
                                        True
                                    )
                                
                                # 调用回调函数（只在有文本时调用）
                                if on_audio and text:
                                    on_audio(audio_data, text)
                            else:
                                # 仅有文本没有音频的情况（剩余文本显示）
                                if on_audio and text:
                                    on_audio(None, text)
                            
                            # 生成数据（流式TTS：多个audio_data对应一个text）
                            self.logger.debug(f"✅ Yield: audio={has_audio}, text='{text[:20] if text else '(empty)'}'")
                            yield (audio_data, text)
                        
                        # ⚠️ 关键：只有收到空的LAST（结束信号）才退出
                        # 如果LAST带有音频/文本，说明还有数据要处理
                        if sentence_type == SentenceType.LAST and not audio_data and not text:
                            self.logger.info("📭 收到空LAST信号，TTS流结束")
                            break
                            
                    await asyncio.sleep(0.01)  # 避免过度占用CPU
            
            # 并发执行两个任务
            feed_task = asyncio.create_task(feed_llm_stream())
            
            # 生成音频流
            async for audio_data, text in get_audio_stream():
                yield (audio_data, text)
                
            # 等待LLM流处理完成
            await feed_task
            
        finally:
            # 停止TTS处理线程
            self.provider.stop_processing()
            
            # 清空所有TTS队列（文本队列和音频队列）
            # 无论正常结束还是打断，都清空残留数据
            if hasattr(self.provider, 'clear_audio_queue'):
                cleared = self.provider.clear_audio_queue()
                if cleared > 0:
                    self.logger.info(f"🧹 [TTS] 清理完成，丢弃了 {cleared} 项残留数据")
    
    async def text_to_speech(self, text: str, play_local: Optional[bool] = None) -> AsyncGenerator[AudioData, None]:
        """文本转语音接口（统一流式接口）
        
        这是对外的唯一接口，无论底层是流式还是非流式TTS，都通过异步生成器返回。
        
        Args:
            text: 要转换的文本
            play_local: 是否本地播放（None则使用配置）
            
        Yields:
            AudioData对象
            - 流式TTS: 边合成边yield（多个chunk）
            - 非流式TTS: 合成完成后yield（单个完整音频）
            
        Example:
            # 流式和非流式统一使用方式
            async for audio_chunk in tts.text_to_speech("你好世界"):
                await send_to_frontend(audio_chunk)
        """
        session_id = str(uuid.uuid4())
        
        # 决定是否本地播放
        if play_local is None:
            play_local = self.config.get("local_play", {}).get("enabled", False)
        
        # 启动TTS处理
        self.provider.start_processing()
        
        try:
            # 发送开始信号
            start_message = TTSMessageDTO(
                sentence_id=session_id,
                sentence_type=SentenceType.FIRST,
                content_type=ContentType.ACTION
            )
            self.provider.put_text(start_message)
            
            # 发送文本
            text_message = TTSMessageDTO(
                sentence_id=session_id,
                sentence_type=SentenceType.LAST,  # 单次转换，直接标记为LAST
                content_type=ContentType.TEXT,
                content_detail=text
            )
            self.provider.put_text(text_message)
            
            # 从队列获取音频并yield
            while True:
                result = await asyncio.get_event_loop().run_in_executor(
                    self.executor,
                    self.provider.get_audio,
                    1.0
                )
                
                if result:
                    sentence_type, audio_data, _ = result
                    
                    if audio_data:
                        # 本地播放（如果启用）
                        if play_local:
                            play_audio(
                                audio_data.data,
                                audio_data.format,
                                True
                            )
                        
                        # 立即yield（流式和非流式都一样）
                        yield audio_data
                    
                    # 如果是最后一个，结束
                    if sentence_type == SentenceType.LAST:
                        break
                        
                await asyncio.sleep(0.01)
                
        finally:
            # 停止处理
            self.provider.stop_processing()
    
    async def aclose(self):
        """异步关闭资源"""
        self.executor.shutdown(wait=True)
        await self.provider.close()
        # 关闭音频播放器
        try:
            from .audio_player import get_audio_player
        except ImportError:
            from audio_player import get_audio_player
        player = get_audio_player()
        player.close()
    
    def close(self):
        """同步关闭资源"""
        self.executor.shutdown(wait=True)
        # 关闭音频播放器
        try:
            from .audio_player import get_audio_player
        except ImportError:
            from audio_player import get_audio_player
        player = get_audio_player()
        player.close()
        # 如果在事件循环中，创建任务；否则直接运行
        try:
            loop = asyncio.get_running_loop()
            # 在运行的事件循环中，创建任务
            asyncio.create_task(self.provider.close())
        except RuntimeError:
            # 没有运行的事件循环，直接运行
            asyncio.run(self.provider.close())


# 便捷函数
async def create_stream_tts(config_path: Optional[str] = None) -> StreamTTS:
    """创建StreamTTS实例的便捷函数"""
    return StreamTTS(config_path)


# 使用示例
async def example_usage():
    """示例：如何使用StreamTTS"""
    import asyncio
    
    # 模拟LLM流式输出
    async def mock_llm_stream():
        texts = [
            "你好，", "我是", "AI助手。", 
            "今天", "天气", "真不错！",
            "有什么", "可以", "帮助你的吗？"
        ]
        for text in texts:
            yield text
            await asyncio.sleep(0.1)
    
    # 创建TTS实例
    tts = StreamTTS()
    
    # 处理流式输出
    async for audio_data, text in tts.process_llm_stream(mock_llm_stream()):
        print(f"生成音频: {text} (大小: {len(audio_data.data)} bytes)")
        # 这里可以播放音频或发送给客户端
    
    tts.close()


if __name__ == "__main__":
    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 运行示例
    asyncio.run(example_usage())
