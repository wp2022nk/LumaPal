"""工具注册表。

YAML 里只写工具名称，例如 generate_cover 或 web_search；这里负责把名称
转换成真实的 LangChain tool 对象。这样新增工具时只需要在本包注册一次，
主智能体和子智能体都能通过配置引用。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .image import generate_cover, generate_social_image
from .web import web_search


TOOL_REGISTRY: Mapping[str, Any] = {
    "generate_cover": generate_cover,
    "generate_social_image": generate_social_image,
    "web_search": web_search,
}


def get_tools(names: list[str] | tuple[str, ...] | None) -> list[Any]:
    """把配置中的工具名列表解析成真实工具对象。

    参数：
        names: YAML 中声明的工具名列表。

    返回：
        LangChain tool 对象列表。

    异常：
        KeyError: 当配置引用了未注册工具时抛出，并给出可用工具名。
    """

    tools = []
    for name in names or []:
        if name not in TOOL_REGISTRY:
            available = ", ".join(sorted(TOOL_REGISTRY))
            raise KeyError(f"Unknown tool '{name}'. Available tools: {available}")
        tools.append(TOOL_REGISTRY[name])
    return tools
