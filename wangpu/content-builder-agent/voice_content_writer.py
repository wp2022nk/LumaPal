"""语音版内容创作 Agent 控制台入口。

这个入口不替换原有 content_writer.py，而是在其基础上额外串起：
音频文件 ASR -> 当前主智能体异步流式回复 -> 主智能体回复分段情绪识别 -> Qwen TTS 播放。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from content_builder.agent_factory import create_content_writer
from content_builder.config import DEFAULT_THREAD_ID, load_main_config
from content_builder.streaming import configure_console_encoding
from content_builder.voice.console import interactive_voice_chat, run_voice_turn


async def run_once(
    task: str,
    *,
    thread_id: str = DEFAULT_THREAD_ID,
    images: list[str] | None = None,
) -> None:
    """执行单轮语音输出任务。"""

    configure_console_encoding()
    runtime_config = load_main_config()
    agent = create_content_writer(runtime_mode="cli")
    await run_voice_turn(
        task,
        agent=agent,
        config=runtime_config,
        thread_id=thread_id,
        images=images,
    )


def main() -> None:
    """CLI 主入口。"""

    configure_console_encoding()
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(
            description="运行语音版 Content Builder Agent。可用 --image 多次传入图片。",
        )
        parser.add_argument(
            "--image",
            action="append",
            default=[],
            help="图片 URL、data URL 或本地图片路径；可重复传入多张图片。",
        )
        parser.add_argument("task", nargs="*", help="要交给 Agent 的任务文本。")
        args = parser.parse_args()
        asyncio.run(run_once(" ".join(args.task), images=args.image))
        return

    asyncio.run(interactive_voice_chat())


if __name__ == "__main__":
    main()
