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
from content_builder.streaming import ConsoleStreamPrinter, configure_console_encoding, stream_agent_events


def run_once(
    task: str,
    *,
    thread_id: str = DEFAULT_THREAD_ID,
    config_path: str | None = None,
    images: list[str] | None = None,
) -> None:
    """运行一次任务并把流式过程打印到控制台。

    参数：
        task: 用户任务文本。
        thread_id: 会话 ID；同一进程内相同 ID 会共享 LangGraph checkpointer 状态。
        config_path: 可选配置文件路径，例如 main_agent.demo.yaml。
        images: 可选图片引用列表。每个元素可以是 http(s) URL、data URL 或本地图片路径；
            它们会和 task 一起作为多模态 user message 交给 Deep Agents。
    """

    configure_console_encoding()
    runtime_config = load_main_config(config_path)
    agent = create_content_writer(config_path)
    resolved_thread_id = thread_id or runtime_config.thread_id or DEFAULT_THREAD_ID
    printer = ConsoleStreamPrinter()
    for event in stream_agent_events(
        agent,
        task,
        thread_id=resolved_thread_id,
        max_turns=runtime_config.conversation.max_turns,
        images=images,
    ):
        printer.print(event)
    printer.finish()

    print("\n\n=== 本地文件位置说明 ===")
    print(f"Deep Agents 虚拟路径 / 会映射到: {PROJECT_DIR}")
    print(f"生成产物通常保存到: {runtime_config.output_root}")
    print(f"研究资料通常保存到: {runtime_config.output_root / 'research'}")


def main() -> None:
    """CLI 主入口。

    有命令行参数时执行单次任务；没有参数时进入控制台多轮交互。
    """

    configure_console_encoding()

    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(
            description="运行内容写作 Deep Agent。可用 --image 多次传入图片。",
        )
        parser.add_argument(
            "--config",
            default=None,
            help="可选的 main_agent.yaml 路径，例如 main_agent.demo.yaml。",
        )
        parser.add_argument(
            "--image",
            action="append",
            default=[],
            help="图片 URL、data URL 或本地图片路径；可重复传入多张图片。",
        )
        parser.add_argument("task", nargs="*", help="要交给 Agent 的任务文本。")
        args = parser.parse_args()
        task = " ".join(args.task).strip()
        if not task:
            interactive_chat(config_path=args.config)
            return
        run_once(task, config_path=args.config, images=args.image)
        return

    interactive_chat()


if __name__ == "__main__":
    main()
