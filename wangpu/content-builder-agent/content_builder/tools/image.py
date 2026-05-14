"""图片生成工具。

这个文件只放和 Qwen 图片生成相关的 LangChain tools。工具内部仍然复用
项目原有的 qwen_image_tool.generate_qwen_image，避免把下载重试等细节复制一份。
"""

from __future__ import annotations

import os
from pathlib import Path

from langchain.tools import tool

from content_builder.config import load_main_config
from qwen_image_tool import DEFAULT_QWEN_IMAGE_MODEL, generate_qwen_image


QWEN_IMAGE_MODEL = os.environ.get("QWEN_IMAGE_MODEL", DEFAULT_QWEN_IMAGE_MODEL)


def output_root() -> Path:
    root = load_main_config().output_root
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_path_segment(value: str, default: str = "image") -> str:
    """把模型传入的目录名压成安全的单段路径。

    Agent 可能把标题、平台名或 slug 直接作为路径片段。这里只保留常见安全字符，
    避免生成包含路径分隔符的文件名。
    """

    cleaned = "".join(
        char for char in str(value) if char.isalnum() or char in {"-", "_", "."}
    ).strip("._")
    return cleaned or default


def save_qwen_image_for_tool(
    *,
    tool_name: str,
    prompt: str,
    output_path: Path,
    error_path: Path,
) -> str:
    """统一的 Qwen 图片生成入口。

    失败时不向 Agent 抛异常，而是写入错误文件并返回可读错误。这样 Agent 可以
    在最终回复里明确说明失败原因，而不会把整个工作流打断在工具异常上。
    """

    try:
        print(
            f"\n[tool:{tool_name}] 使用 {QWEN_IMAGE_MODEL} 生成并自动下载图片: "
            f"{output_path}",
            flush=True,
        )
        result = generate_qwen_image(
            prompt,
            output_path,
            model=QWEN_IMAGE_MODEL,
        )
        print(
            f"[tool:{tool_name}] 图片已保存: {output_path} "
            f"({result.get('bytes', 0)} bytes)",
            flush=True,
        )
        return f"Image saved to {output_path}"
    except Exception as exc:
        error_path.parent.mkdir(parents=True, exist_ok=True)
        error = (
            "Image generation failed; local image was not saved. "
            f"Reason: {exc}"
        )
        error_path.write_text(error, encoding="utf-8")
        print(f"[tool:{tool_name}] {error}", flush=True)
        return error


@tool
def generate_cover(prompt: str, slug: str) -> str:
    """为博客文章生成封面图。

    参数：
        prompt: 图片生成提示词，应描述风格、主题、构图、色彩等。
        slug: 博客文章 slug，图片会保存到输出工作区下的 blogs/<slug>/hero.png。
    """

    safe_slug = safe_path_segment(slug, "blog")
    root = output_root()
    output_path = root / "blogs" / safe_slug / "hero.png"
    error_path = root / "blogs" / safe_slug / "hero-error.txt"
    return save_qwen_image_for_tool(
        tool_name="generate_cover",
        prompt=prompt,
        output_path=output_path,
        error_path=error_path,
    )


@tool
def generate_social_image(prompt: str, platform: str, slug: str) -> str:
    """为社交媒体帖子生成配图。

    参数：
        prompt: 图片生成提示词，应描述目标平台需要的视觉效果。
        platform: 社交平台目录名，例如 linkedin 或 tweets。
        slug: 帖子 slug，图片会保存到输出工作区下的 <platform>/<slug>/image.png。
    """

    safe_platform = safe_path_segment(platform, "social")
    safe_slug = safe_path_segment(slug, "post")
    root = output_root()
    output_path = root / safe_platform / safe_slug / "image.png"
    error_path = root / safe_platform / safe_slug / "image-error.txt"
    return save_qwen_image_for_tool(
        tool_name="generate_social_image",
        prompt=prompt,
        output_path=output_path,
        error_path=error_path,
    )
