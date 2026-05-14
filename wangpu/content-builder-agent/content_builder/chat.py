"""对外聊天入口。

这个文件是外部调用方最常接触的层：可以拿结构化事件做 UI，也可以直接调用
chat_once 得到最终文本，或者启动本地控制台多轮对话。
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Iterator

# 当用户直接运行 python content_builder/chat.py 时，Python 不会把它识别为包内模块，
# 因此相对导入会失败。这里在脚本模式下把项目根目录加入 sys.path，并改用绝对导入。
# 推荐入口仍然是 content_writer.py 或 python -m content_builder.chat；这个分支只是让
# 直接运行文件也有一个友好的行为。
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from content_builder.agent_factory import create_content_writer
    from content_builder.config import DEFAULT_THREAD_ID, MainAgentConfig, load_main_config
    from content_builder.streaming import StreamEvent, print_stream_event, stream_agent_events
else:
    from .agent_factory import create_content_writer
    from .config import DEFAULT_THREAD_ID, MainAgentConfig, load_main_config
    from .streaming import StreamEvent, print_stream_event, stream_agent_events


def _load_runtime_config(config_path: str | Path | None) -> MainAgentConfig:
    """读取运行时配置。

    chat 层需要 thread_id 和 conversation.max_turns；agent_factory 也会读同一份
    配置来构建 Agent。这里单独加载一次只是为了把运行参数传给 streaming 层。
    """

    return load_main_config(config_path)


def _resolve_thread_id(config_path: str | Path | None, thread_id: str | None) -> str:
    """解析本次会话使用的 thread_id。

    LangGraph 的 checkpointer 依赖 thread_id 区分会话。控制台多轮必须复用同一个
    thread_id，否则每一轮都会变成全新的对话。
    """

    if thread_id:
        return thread_id
    return _load_runtime_config(config_path).thread_id or DEFAULT_THREAD_ID


def stream_chat(
    message: str,
    *,
    thread_id: str = DEFAULT_THREAD_ID,
    config_path: str | Path | None = None,
    images: Iterable[str | Path] | None = None,
) -> Iterator[StreamEvent]:
    """对外暴露的结构化流式输入端口。

    参数：
        message: 用户输入。
        images: 可选图片 URL、data URL 或本地图片路径。传入后会和文本组成同一条
            多模态 user message，直接交给 Deep Agents 使用的多模态模型。
        thread_id: 会话 ID；相同 ID 在同一进程内共享上下文。
        config_path: 可选配置文件路径。

    返回：
        StreamEvent 迭代器，调用方可以自行决定如何展示 token、工具和任务事件。
    """

    runtime_config = _load_runtime_config(config_path)
    agent = create_content_writer(config_path)
    resolved_thread_id = thread_id or runtime_config.thread_id or DEFAULT_THREAD_ID
    yield from stream_agent_events(
        agent,
        message,
        thread_id=resolved_thread_id,
        max_turns=runtime_config.conversation.max_turns,
        images=images,
    )


def chat_once(
    message: str,
    *,
    thread_id: str = DEFAULT_THREAD_ID,
    config_path: str | Path | None = None,
    images: Iterable[str | Path] | None = None,
) -> str:
    """运行一次对话并返回最终文本。

    这个函数适合脚本或测试调用。它会消费完整流，但不会自动打印过程事件。
    """

    final_text = ""
    for event in stream_chat(message, thread_id=thread_id, config_path=config_path, images=images):
        if event.type == "final":
            final_text = event.text
    return final_text


def interactive_chat(
    config_path: str | Path | None = None,
    thread_id: str = DEFAULT_THREAD_ID,
) -> None:
    """启动控制台多轮交互。

    同一个 agent 实例和同一个 thread_id 会贯穿整个控制台会话，因此用户可以连续
    追问，模型也能看到前面轮次的上下文。输入 exit 或 quit 退出。
    """

    runtime_config = _load_runtime_config(config_path)
    agent = create_content_writer(config_path)
    resolved_thread_id = thread_id or runtime_config.thread_id or DEFAULT_THREAD_ID

    print("Content Builder Agent 已启动。输入 exit 或 quit 退出。")
    print(f"当前 thread_id: {resolved_thread_id}")
    print("图片输入：先输入 /image 图片路径 或 /image 图片URL，下一条普通消息会自动带上图片。")
    print("图片管理：/images 查看待发送图片，/clear-images 清空待发送图片。")

    # 交互式命令行无法像 GUI 一样上传附件，所以这里采用“待发送图片队列”：
    # 用户先用 /image 添加一个或多个本地路径/URL，随后输入普通文本时，streaming 层会把
    # 图片和文本组装成同一条多模态 user message。发送成功后清空队列，避免下一轮误带旧图。
    pending_images: list[str] = []

    while True:
        try:
            message = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return

        if not message:
            continue
        if message.lower() in {"exit", "quit"}:
            print("已退出。")
            return

        command = message.lower()
        if command.startswith("/image "):
            # 取 /image 后面的整段文本作为一个图片引用。这样 Windows 路径中即使包含空格，
            # 也不需要额外解析成多个参数；如果要传多张图，重复输入多次 /image 即可。
            image = message[len("/image ") :].strip().strip('"').strip("'")
            if not image:
                print("用法：/image 图片路径 或 /image 图片URL")
                continue
            pending_images.append(image)
            print(f"已添加图片，将随下一条消息发送：{image}")
            continue

        if command == "/images":
            if not pending_images:
                print("当前没有待发送图片。")
                continue
            print("待发送图片：")
            for index, image in enumerate(pending_images, start=1):
                print(f"  {index}. {image}")
            continue

        if command == "/clear-images":
            pending_images.clear()
            print("已清空待发送图片。")
            continue

        images_for_turn = list(pending_images)
        try:
            for event in stream_agent_events(
                agent,
                message,
                thread_id=resolved_thread_id,
                max_turns=runtime_config.conversation.max_turns,
                images=images_for_turn,
            ):
                print_stream_event(event)
        except (FileNotFoundError, ValueError) as exc:
            # 路径不存在或文件类型不对时，保留待发送图片，方便用户 /clear-images 后重输；
            # 不把这类本地输入错误吞成模型错误，交互体验会清楚很多。
            print(f"\n图片输入错误：{exc}")
            continue

        if images_for_turn:
            pending_images.clear()
        print()


if __name__ == "__main__":
    interactive_chat()
