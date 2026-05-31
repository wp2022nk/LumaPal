"""音频播放器模块 - 支持本地播放音频"""
import os
import sys
import logging
import threading
import queue
from typing import Optional

logger = logging.getLogger(__name__)


class AudioPlayer:
    """跨平台音频播放器"""
    
    def __init__(self):
        self.player_available = False
        self.player_type = None
        self._init_player()
        
        # 播放队列和线程
        self.play_queue = queue.Queue()
        self.play_thread = None
        self.stop_event = threading.Event()
        self._start_play_thread()
    
    def _init_player(self):
        """初始化音频播放器"""
        # 优先尝试使用pygame（最兼容）
        try:
            import pygame
            pygame.mixer.init()
            self.player_available = True
            self.player_type = "pygame"
            logger.info("使用pygame作为音频播放器")
            return
        except ImportError:
            logger.debug("pygame不可用，尝试其他播放器")
        
        # 尝试使用playsound（比较兼容）
        try:
            import playsound
            self.player_available = True
            self.player_type = "playsound"
            logger.info("使用playsound作为音频播放器")
            return
        except ImportError:
            logger.debug("playsound不可用，尝试其他播放器")
        
        # Windows系统尝试使用系统播放器（更兼容各种格式）
        if sys.platform == "win32":
            try:
                import os
                # 测试系统播放器是否可用
                self.player_available = True
                self.player_type = "system"
                logger.info("使用Windows系统播放器")
                return
            except:
                pass
                
            # 最后尝试winsound（仅支持WAV）
            try:
                import winsound
                self.player_available = True
                self.player_type = "winsound"
                logger.info("使用winsound作为音频播放器（仅支持WAV格式）")
                return
            except ImportError:
                pass
        
        logger.warning("未找到可用的音频播放器，本地播放功能将被禁用")
    
    def _start_play_thread(self):
        """启动播放线程"""
        if self.play_thread and self.play_thread.is_alive():
            return
            
        self.play_thread = threading.Thread(target=self._play_worker, daemon=True)
        self.play_thread.start()
        logger.debug("音频播放线程已启动")
    
    def _play_worker(self):
        """播放工作线程 - 按顺序播放队列中的音频"""
        while not self.stop_event.is_set():
            try:
                # 获取播放任务（超时1秒）
                task = self.play_queue.get(timeout=1)
                if task is None:  # 停止信号
                    break
                    
                file_path, temp_file = task
                
                # 播放音频文件（同步等待播放完成）
                try:
                    self._play_audio_file_sync(file_path)
                except Exception as e:
                    logger.error(f"播放音频失败: {e}")
                
                # 清理临时文件
                if temp_file and os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except:
                        pass
                        
            except queue.Empty:
                continue
    
    def play_audio_data(self, audio_data: bytes, format: str = "mp3") -> bool:
        """播放音频数据（加入队列）
        
        Args:
            audio_data: 音频二进制数据
            format: 音频格式
            
        Returns:
            是否成功加入播放队列
        """
        if not self.player_available:
            return False
        
        # 保存临时文件
        import tempfile
        
        try:
            # 创建临时文件
            with tempfile.NamedTemporaryFile(suffix=f".{format}", delete=False) as f:
                f.write(audio_data)
                temp_file = f.name
            
            # 加入播放队列
            self.play_queue.put((temp_file, True))
            return True
            
        except Exception as e:
            logger.error(f"创建临时音频文件失败: {e}")
            return False
    
    def play_audio_file(self, file_path: str) -> bool:
        """播放音频文件（加入队列）
        
        Args:
            file_path: 音频文件路径
            
        Returns:
            是否成功加入播放队列
        """
        if not self.player_available:
            return False
            
        try:
            # 加入播放队列（不是临时文件）
            self.play_queue.put((file_path, False))
            return True
        except Exception as e:
            logger.error(f"加入播放队列失败: {e}")
            return False
    
    def _play_audio_file_sync(self, file_path: str) -> bool:
        """同步播放音频文件（内部使用）
        
        Args:
            file_path: 音频文件路径
            
        Returns:
            是否播放成功
        """
        if not self.player_available:
            return False
        
        try:
            if self.player_type == "pygame":
                import pygame
                pygame.mixer.music.load(file_path)
                pygame.mixer.music.play()
                # 等待播放完成
                while pygame.mixer.music.get_busy():
                    pygame.time.Clock().tick(10)
                    
            elif self.player_type == "playsound":
                import playsound
                playsound.playsound(file_path)
                
            elif self.player_type == "system":
                # 使用系统默认播放器
                import os
                import time
                if sys.platform == "win32":
                    # Windows: 使用同步播放
                    os.system(f'start /wait "" "{file_path}"')
                elif sys.platform == "darwin":  # macOS
                    os.system(f"afplay '{file_path}'")  # afplay是同步的
                else:  # Linux
                    os.system(f"aplay '{file_path}' 2>/dev/null || mpg123 '{file_path}' 2>/dev/null")
                
            elif self.player_type == "winsound":
                import winsound
                # winsound只支持WAV格式
                if file_path.lower().endswith('.wav'):
                    winsound.PlaySound(file_path, winsound.SND_FILENAME)
                else:
                    logger.warning(f"winsound不支持{file_path}格式，跳过播放")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"播放音频失败: {e}")
            return False
    
    def is_available(self) -> bool:
        """检查播放器是否可用"""
        return self.player_available
    
    def clear_queue(self):
        """
        清空播放队列
        
        使用场景：打断功能触发时，清空所有待播放的音频
        注意：不会停止当前正在播放的音频，只清空队列
        """
        cleared_count = 0
        while not self.play_queue.empty():
            try:
                self.play_queue.get_nowait()
                cleared_count += 1
            except:
                break
        
        if cleared_count > 0:
            logger.info(f"🧹 已清空音频队列，丢弃 {cleared_count} 个待播放音频")
        return cleared_count
    
    def stop_current_playback(self):
        """
        停止当前正在播放的音频
        
        使用场景：打断功能触发时，立即停止当前播放
        注意：不同的播放器类型支持程度不同
        """
        try:
            if self.player_type == "pygame":
                import pygame
                if pygame.mixer.music.get_busy():
                    pygame.mixer.music.stop()
                    logger.info("🛑 已停止当前播放 (pygame)")
            
            elif self.player_type == "playsound":
                # playsound不支持停止，只能等待播放完成
                logger.warning("⚠️ playsound不支持停止当前播放")
            
            elif self.player_type == "system":
                # 系统播放器难以控制，暂不支持
                logger.warning("⚠️ 系统播放器不支持停止当前播放")
            
            elif self.player_type == "winsound":
                import winsound
                # winsound可以通过播放静音停止
                winsound.PlaySound(None, winsound.SND_PURGE)
                logger.info("🛑 已停止当前播放 (winsound)")
                
        except Exception as e:
            logger.error(f"❌ 停止播放失败: {e}")
    
    def interrupt_playback(self):
        """
        打断播放（清空队列+停止当前播放）
        
        这是打断功能的便捷方法，同时执行：
        1. 清空待播放队列
        2. 停止当前正在播放的音频
        """
        logger.info("🛑 触发播放打断")
        self.clear_queue()
        self.stop_current_playback()
    
    def close(self):
        """关闭播放器，清理资源"""
        # 停止播放线程
        self.stop_event.set()
        if self.play_thread:
            self.play_queue.put(None)  # 发送停止信号
            self.play_thread.join(timeout=2)
        logger.debug("音频播放器已关闭")


# 全局播放器实例
_global_player = None


def get_audio_player() -> AudioPlayer:
    """获取全局音频播放器实例"""
    global _global_player
    if _global_player is None:
        _global_player = AudioPlayer()
    return _global_player


def play_audio(audio_data: bytes, format: str = "mp3", enabled: bool = True) -> bool:
    """便捷函数：播放音频数据
    
    Args:
        audio_data: 音频数据
        format: 音频格式
        enabled: 是否启用播放（方便通过配置关闭）
        
    Returns:
        是否播放成功
    """
    if not enabled:
        return False
        
    player = get_audio_player()
    return player.play_audio_data(audio_data, format)
