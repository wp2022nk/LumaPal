"""内容写作 Agent 的可复用 Python 接口。

这个包把原来集中在 content_writer.py 里的逻辑拆成了几层：
配置读取、工具定义、Agent 构建、流式事件处理和对外聊天入口。
外部代码一般只需要从这里导入 create_content_writer、stream_chat 或 chat_once。

注意：这里使用懒加载，不在包导入时立刻加载 deepagents/langchain 等重依赖。
这样像 content_builder.streaming 这种轻量模块可以被单独测试，也能让依赖缺失时
错误发生在真正创建 Agent 的时候，而不是 import 包名时。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_factory import create_content_writer
    from .chat import chat_once, interactive_chat, stream_chat
    from .streaming import StreamEvent


def __getattr__(name: str):
    """按需导入公共接口。

    Python 的 from content_builder import chat_once 会触发这里。懒加载可以减少
    单元测试和工具脚本的依赖压力，同时保持原计划中的公共导入路径不变。
    """

    if name == "StreamEvent":
        from .streaming import StreamEvent

        return StreamEvent

    if name == "create_content_writer":
        from .agent_factory import create_content_writer

        return create_content_writer

    if name in {"chat_once", "interactive_chat", "stream_chat"}:
        from .chat import chat_once, interactive_chat, stream_chat

        return {
            "chat_once": chat_once,
            "interactive_chat": interactive_chat,
            "stream_chat": stream_chat,
        }[name]

    raise AttributeError(f"module 'content_builder' has no attribute {name!r}")

__all__ = [
    "StreamEvent",
    "chat_once",
    "create_content_writer",
    "interactive_chat",
    "stream_chat",
]
