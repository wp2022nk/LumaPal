"""多模态用户输入构建工具。

这个模块只负责把“用户文本 + 图片引用”整理成 LangChain/ChatQwen 能理解的
message.content。它不调用模型、不做图片识别，也不新增 Deep Agents tool。
这样主 Agent、子 Agent、工具列表都保持原来的职责：图片理解由当前多模态大模型
在一次普通 user message 中完成。
"""

from __future__ import annotations

import base64
import mimetypes
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ImageInput = str | Path
UserContent = str | list[dict[str, Any]]


def _is_http_url(value: str) -> bool:
    """判断图片引用是否是模型可直接访问的公网 URL。"""

    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_image_data_url(value: str) -> bool:
    """判断调用方是否已经传入完整的图片 data URL。

    Qwen 的 OpenAI-compatible 接口不支持直接读取本机路径；本地图片必须先转成
    data:image/...;base64,... 这种 URL 字符串。若上游已经完成编码，这里只做
    最小校验并原样透传，避免重复编码造成体积膨胀。
    """

    return value.startswith("data:image/") and ";base64," in value


def _image_url_block(url: str) -> dict[str, Any]:
    """生成 ChatQwen 官方 Vision 示例使用的 provider-native 图片块。"""

    return {"type": "image_url", "image_url": {"url": url}}


def _local_image_to_data_url(image_path: Path) -> str:
    """把本地图片编码成 OpenAI-compatible 接口可接受的 data URL。

    官方 Qwen 文档里“直接传本地 file:// 路径”只适用于 DashScope Python/Java SDK，
    不适用于 HTTP 或 OpenAI-compatible 接口。当前项目使用的是 ChatQwen + compatible
    endpoint，所以这里统一把本地图片转成 base64 data URL，确保模型服务端能收到
    图片字节，而不是收到一个它无法访问的本机路径。
    """

    resolved_path = image_path.expanduser().resolve()
    if not resolved_path.is_file():
        raise FileNotFoundError(f"Image file not found: {resolved_path}")

    mime_type, _ = mimetypes.guess_type(str(resolved_path))
    if not mime_type or not mime_type.startswith("image/"):
        raise ValueError(
            "Unsupported image file type. Expected a PNG/JPEG/WebP/GIF or other image file: "
            f"{resolved_path}"
        )

    encoded = base64.b64encode(resolved_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _image_to_block(image: ImageInput) -> dict[str, Any]:
    """把单个图片输入转换成 content block。

    支持三种来源：
    1. http(s) URL：直接交给模型服务端下载；
    2. data:image/...;base64,...：调用方已编码，原样透传；
    3. 本地路径：读取文件并转成 data URL。
    """

    if isinstance(image, Path):
        return _image_url_block(_local_image_to_data_url(image))

    image_value = str(image).strip()
    if not image_value:
        raise ValueError("Image input cannot be empty")

    if _is_http_url(image_value) or _is_image_data_url(image_value):
        return _image_url_block(image_value)

    return _image_url_block(_local_image_to_data_url(Path(image_value)))


def build_user_content(text: str, images: Iterable[ImageInput] | None = None) -> UserContent:
    """构建 Deep Agents user message 的 content。

    没有图片时返回原始字符串，这是一个刻意保留的兼容行为：Deep Agents、LangGraph
    checkpoint、既有测试脚本和日志函数都已经围绕纯文本输入工作，继续返回 str 可以
    避免无图请求发生任何序列化差异。

    有图片时返回 content block 列表，并使用 ChatQwen 官方 Vision 示例中的
    ``image_url`` 块格式。我们把图片和文本放在同一条 user message 中，模型就能在
    同一次推理里结合视觉信息、上下文记忆和 Deep Agents 的工具能力，而不需要额外
    注册一个“图片识别工具”。
    """

    image_list = list(images or [])
    if not image_list:
        return text

    blocks = [_image_to_block(image) for image in image_list]
    blocks.append({"type": "text", "text": text})
    return blocks
