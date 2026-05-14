"""内容写作 Agent 的命令行入口。

这个文件现在只负责接收命令行输入并调用 content_builder 包里的公共接口。
真正的配置加载、工具定义、Agent 构建和流式打印逻辑都已经拆分到独立模块，
便于后续扩展和测试。
"""

from __future__ import annotations

import argparse
import sys

from content_builder import interactive_chat
from content_builder.agent_factory import create_content_writer
from content_builder.config import DEFAULT_THREAD_ID, PROJECT_DIR, load_main_config
from content_builder.streaming import print_stream_event, stream_agent_events


def run_once(
    task: str,
    *,
    thread_id: str = DEFAULT_THREAD_ID,
    images: list[str] | None = None,
) -> None:
    """运行一次任务并把流式过程打印到控制台。

    参数：
        task: 用户任务文本。
        thread_id: 会话 ID；同一进程内相同 ID 会共享 LangGraph checkpointer 状态。
        images: 可选图片引用列表。每个元素可以是 http(s) URL、data URL 或本地图片路径；
            它们会和 task 一起作为多模态 user message 交给 Deep Agents。
    """

    runtime_config = load_main_config()
    agent = create_content_writer()
    for event in stream_agent_events(
        agent,
        task,
        thread_id=thread_id,
        max_turns=runtime_config.conversation.max_turns,
        images=images,
    ):
        print_stream_event(event)

    print("\n\n=== 本地文件位置说明 ===")
    print(f"Deep Agents 虚拟路径 / 会映射到: {PROJECT_DIR}")
    print(f"博客文件通常保存到: {PROJECT_DIR / 'blogs'}")
    print(f"研究资料通常保存到: {PROJECT_DIR / 'research'}")


def main() -> None:
    """CLI 主入口。

    有命令行参数时执行单次任务；没有参数时进入控制台多轮交互。
    """

    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(
            description="运行内容写作 Deep Agent。可用 --image 多次传入图片。",
        )
        parser.add_argument(
            "--image",
            action="append",
            default=[],
            help="图片 URL、data URL 或本地图片路径；可重复传入多张图片。",
        )
        parser.add_argument("task", nargs="*", help="要交给 Agent 的任务文本。")
        args = parser.parse_args()
        run_once(" ".join(args.task), images=args.image)
        return

    interactive_chat()


if __name__ == "__main__":
    main()
