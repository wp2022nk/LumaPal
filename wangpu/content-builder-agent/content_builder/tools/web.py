"""联网搜索工具。

这个模块只定义搜索工具本身，不参与 Agent 构建。工具按需导入 Tavily，
这样即使本地没有安装 tavily 包，其他不需要搜索的流程仍然可以 import。
"""

from __future__ import annotations

import os
from typing import Literal

from langchain.tools import tool


@tool
def web_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news"] = "general",
) -> dict:
    """搜索实时网页信息。

    参数：
        query: 搜索关键词，建议尽量具体、清晰。
        max_results: 返回的搜索结果数量，默认返回 5 条。
        topic: 搜索主题，general 适合多数问题，news 适合新闻和近期事件。

    返回：
        Tavily 原始搜索结果；如果失败则返回包含 error 字段的字典。
    """

    try:
        from tavily import TavilyClient

        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            return {"error": "TAVILY_API_KEY not set"}

        print(f"\n[tool:web_search] 开始搜索: {query}", flush=True)
        client = TavilyClient(api_key=api_key)
        result = client.search(query, max_results=max_results, topic=topic)
        print(
            f"[tool:web_search] 搜索完成，返回 {len(result.get('results', []))} 条结果",
            flush=True,
        )
        return result
    except Exception as exc:
        # 工具函数把异常转成结构化结果，Agent 可以据此调整策略或向用户说明失败。
        error = f"Search failed: {exc}"
        print(f"[tool:web_search] {error}", flush=True)
        return {"error": error}
