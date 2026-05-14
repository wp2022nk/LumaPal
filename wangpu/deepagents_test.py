import json
from typing import Literal
from tavily import TavilyClient
from deepagents import create_deep_agent
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_qwq import ChatQwen

# ========== 基础配置 ==========
#
# 这里按你的要求把 Gemini API Key 直接写在源码里，不再依赖 GOOGLE_API_KEY
# 之类的环境变量。生产项目通常更建议放到环境变量或密钥管理服务中，
# 但本测试脚本为了方便本地直接运行，采用硬编码方式。
GEMINI_API_KEY = "AIzaSyA0qZ3yNh2TfRPd5iPvrwRer9rHClc8J0Y"

# Tavily 用作联网搜索工具。这个 client 会被下面的 internet_search 工具函数复用。
tavily_client = TavilyClient(api_key="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")

# 显式创建 Google Gemini 的 LangChain ChatModel 实例，然后传给 create_deep_agent。
# 这样做的好处是可以直接把 api_key、模型名、温度、thinking 等 provider 参数
# 写在这里，而不是只传 "google_genai:xxx" 字符串让底层自动读取环境变量。
gemini_model = ChatGoogleGenerativeAI(
    model="gemini-3.1-flash-lite",
    api_key=GEMINI_API_KEY,
)

qwen_model = ChatQwen(
    model="qwen3.6-plus",
    api_key="sk-24ebca554a394d7e8bc54602e854fdfe",
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)


def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
):
    """执行联网搜索。

    Deep Agents 会把这个 Python 函数包装成一个可调用工具，模型在需要外部
    信息时可以自动触发它。函数签名里的参数类型和默认值会成为工具 schema
    的一部分，帮助模型理解应该如何传参。

    Args:
        query: 搜索关键词。
        max_results: 返回结果数量上限。
        topic: Tavily 搜索类别，可选 general/news/finance。
        include_raw_content: 是否返回网页原始内容；开启后结果更完整但更占 token。
    """
    return tavily_client.search(
        query,
        max_results=max_results,
        include_raw_content=include_raw_content,
        topic=topic,
    )


# ========== Agent 提示词 ==========
#
# system_prompt 用来规定 Deep Agent 的角色、工作方式和工具使用习惯。
# 这里告诉模型：它是研究员，主要通过 internet_search 获取信息。
research_instructions = """You are an expert researcher. Your job is to conduct thorough research and then write a polished report.

You have access to an internet search tool as your primary means of gathering information.

## `internet_search`

Use this to run an internet search for a given query. You can specify the max number of results to return, the topic, and whether raw content should be included.

"""

# ========== 创建 Deep Agent ==========
#
# create_deep_agent 会自动挂载 Deep Agents 的内置能力，例如：
# - write_todos: 复杂任务规划
# - task: 委派子 Agent
# - 文件系统相关工具，取决于 backend 配置
#
# 注意：只用 agent.invoke(...) 时，通常只能看到最终结果；
# 要看到规划、工具调用、子 Agent 过程，需要使用下面的 agent.stream(...)。
agent = create_deep_agent(
    model=qwen_model,
    tools=[internet_search],
    system_prompt=research_instructions,
)


def text_from_content(content) -> str:
    """把 LangChain/Provider 返回的 content 统一转成纯文本。

    不同模型 provider 返回的消息内容格式可能不同：
    - 有些直接返回 str；
    - Gemini 等模型有时返回 content block 列表，例如
      [{"type": "text", "text": "..."}]；
    - 少数情况下可能是其他对象。

    这个函数负责把这些格式规整成可以 print 的字符串，避免终端里直接输出
    一整坨结构化对象。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)


def short_text(content, limit: int = 300) -> str:
    """生成工具结果预览文本。

    搜索工具或子 Agent 的返回内容可能很长，直接打印会刷屏。
    这里把换行压成空格，并限制最大字符数，让日志更容易扫读。
    """
    text = text_from_content(content).replace("\n", " ").strip()
    return text if len(text) <= limit else f"{text[:limit]}..."


def format_args(args) -> str:
    """把工具参数格式化成可读 JSON。

    ensure_ascii=False 可以保留中文原文，不会变成 \\u4e2d\\u6587 这种形式。
    """
    return json.dumps(args, ensure_ascii=False, indent=2)


def print_tool_call(source: str, tool_call: dict) -> None:
    """打印一次工具调用日志。

    Args:
        source: 调用来源，main 表示主 Agent，subagent:xxx 表示子 Agent。
        tool_call: LangChain 消息对象中解析出来的工具调用字典。

    对 write_todos 做了特殊处理，因为它代表 Deep Agents 的规划步骤；
    其他工具则统一打印工具名和参数。
    """
    name = tool_call.get("name", "unknown_tool")
    args = tool_call.get("args", {})

    # write_todos 是 Deep Agents 的内置规划工具。
    # 如果模型认为任务复杂，它会先调用这个工具维护待办列表。
    # 简单问题可能不会触发规划，所以看不到 write_todos 是正常的。
    if name == "write_todos":
        print(f"\n[{source}] planning: write_todos")
        for item in args.get("todos", []):
            print(f"  - [{item.get('status', 'pending')}] {item.get('content', '')}")
        return

    print(f"\n[{source}] tool call: {name}")
    print(format_args(args))


def source_from_namespace(namespace) -> str:
    """根据 stream 事件的 namespace 判断日志来源。

    Deep Agents 基于 LangGraph 的 streaming 机制。开启 subgraphs=True 后：
    - 主 Agent 的 namespace 通常是空元组；
    - 子 Agent 或 task 工具相关事件里会出现类似 "tools:<id>" 的片段。

    这里把这些内部 namespace 转成人更容易读的标签。
    """
    for segment in namespace:
        if isinstance(segment, str) and segment.startswith("tools:"):
            return f"subagent:{segment.split(':', 1)[1]}"
    return "main"


def stream_agent_run(question: str) -> None:
    """以流式模式运行 Agent，并打印规划、工具调用和最终回答。

    agent.invoke(...) 会等整个图执行完后一次性返回最终 state，
    因此你之前看不到中间的规划和工具调用。

    agent.stream(...) 则会持续吐出事件。这里同时打开两个 stream_mode：
    - updates: 每个节点执行完成后的状态更新，适合看 model_request/tools 步骤；
    - messages: 模型 token 和 tool_call_chunks，适合看工具调用的流式参数。

    subgraphs=True 会把子 Agent 的事件也暴露出来；
    version="v2" 使用 Deep Agents 文档推荐的新事件格式。
    """
    final_answer = ""

    for chunk in agent.stream(
        {"messages": [{"role": "user", "content": question}]},
        stream_mode=["updates", "messages"],
        subgraphs=True,
        version="v2",
    ):
        source = source_from_namespace(chunk.get("ns", ()))

        if chunk["type"] == "updates":
            # updates 事件的 data 通常按节点名组织，例如：
            # - model_request: 模型请求节点，常包含 AIMessage 和 tool_calls；
            # - tools: 工具执行节点，常包含 ToolMessage 工具返回结果。
            update_data = chunk.get("data")
            if not isinstance(update_data, dict):
                continue

            for node_name, data in update_data.items():
                if node_name in {"model_request", "tools"}:
                    print(f"\n[{source}] step: {node_name}")

                # 某些 LangGraph/Deep Agents 版本会在 updates 里返回
                # {"节点名": None}，表示该节点有事件但没有可解析的消息载荷。
                # 这种情况下跳过消息解析即可，否则对 None 调用 .get 会报错。
                if not isinstance(data, dict):
                    continue

                for message in data.get("messages", []):
                    # AIMessage 上如果有 tool_calls，说明模型决定调用工具。
                    # 这里会打印 write_todos / internet_search / task 等工具调用。
                    for tool_call in getattr(message, "tool_calls", []) or []:
                        print_tool_call(source, tool_call)

                    # ToolMessage 表示工具已经执行完毕并返回结果。
                    # 对搜索结果、子 Agent 返回值等，只打印简短预览。
                    if getattr(message, "type", "") == "tool":
                        print(f"\n[{source}] tool result: {getattr(message, 'name', 'tool')}")
                        print(short_text(message.content))

                    # 没有 tool_calls 的 AIMessage 通常就是阶段性回答或最终回答。
                    # 流式过程中可能多次出现，这里保留最后一段作为最终输出。
                    if getattr(message, "type", "") == "ai" and not getattr(message, "tool_calls", []):
                        text = text_from_content(message.content).strip()
                        if text:
                            final_answer = text

        elif chunk["type"] == "messages":
            # messages 事件更细，能看到工具调用参数是如何流式生成的。
            # 有些 provider 会把 tool_call 的 name/args 分块输出。
            message_data = chunk.get("data")
            if not isinstance(message_data, tuple) or len(message_data) != 2:
                continue

            token, _metadata = message_data
            for tool_call_chunk in getattr(token, "tool_call_chunks", []) or []:
                if tool_call_chunk.get("name"):
                    print(f"\n[{source}] streaming tool call: {tool_call_chunk['name']}")
                if tool_call_chunk.get("args"):
                    print(tool_call_chunk["args"], end="", flush=True)

    # 所有流式事件结束后，打印最终回答，避免它被中间日志淹没。
    if final_answer:
        print("\n\n=== Final Answer ===")
        print(final_answer)


# 入口：修改这里的问题即可测试不同复杂度的任务。
# 简单问题可能不会触发 write_todos；想看规划日志，可以换成更复杂的研究任务。
stream_agent_run("请深入研究 LangGraph、LangChain 和 Deep Agents 的区别，列出学习路线，并给出示例代码。使用中文")

