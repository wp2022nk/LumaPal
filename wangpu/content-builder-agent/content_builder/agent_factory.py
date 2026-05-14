"""Agent 构建模块。

这个文件把 YAML 配置映射到 create_deep_agent() 的参数。它不负责终端打印，
也不直接处理用户输入；这些职责分别放在 streaming.py 和 chat.py 中。
"""

from __future__ import annotations

import warnings
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any

warnings.filterwarnings(
    "ignore",
    message=r"The default value of `allowed_objects` will change.*",
)

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langgraph.checkpoint.memory import MemorySaver

from .config import (
    DEFAULT_MAIN_CONFIG,
    MainAgentConfig,
    create_qwen_model,
    load_main_config,
    load_subagents_yaml,
    resolve_project_path,
)
from .local_shell_backend import create_confirmed_local_shell_backend
from .tools import get_tools


def _read_system_prompt(config: MainAgentConfig) -> str | None:
    """读取主智能体系统提示词文件。

    Deep Agents 的 memory 会把 /AGENTS.md 暴露给 Agent 文件系统；同时传入
    system_prompt 可以让主角色设定在启动时就生效，不必等模型主动读取文件。
    """

    if not config.system_prompt_file:
        return None
    prompt_path = config.backend.root_dir / config.system_prompt_file.lstrip("/")
    if not prompt_path.exists():
        raise FileNotFoundError(f"System prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _build_subagents(config: MainAgentConfig, main_model: Any) -> list[dict[str, Any]]:
    """从 subagents.yaml 构建 Deep Agents 需要的子智能体列表。

    YAML 中只写工具名和模型名；这里完成两件绑定：
    1. 工具名 -> 真实 LangChain tool 对象。
    2. 与主模型同名的子智能体 -> 复用主模型对象。
    """

    raw_subagents = load_subagents_yaml(config.subagents_config)
    subagents: list[dict[str, Any]] = []

    for name, spec in raw_subagents.items():
        if not isinstance(spec, dict):
            raise ValueError(f"Subagent '{name}' config must be a mapping")

        subagent: dict[str, Any] = {
            "name": name,
            "description": spec["description"],
            "system_prompt": spec["system_prompt"],
        }

        if spec.get("model"):
            subagent["model"] = main_model if spec["model"] == config.model.model else spec["model"]

        if spec.get("tools"):
            subagent["tools"] = get_tools(list(spec["tools"]))

        # Deep Agents 自定义子智能体不会自动继承主智能体 skills。
        # 如果某个子智能体需要独立 skill，直接在 subagents.yaml 里声明 skills 即可。
        if spec.get("skills"):
            subagent["skills"] = list(spec["skills"])

        subagents.append(subagent)

    return subagents


def _build_backend(config: MainAgentConfig) -> Any:
    """根据配置构建 Deep Agents backend。"""

    if config.backend.type == "filesystem":
        return FilesystemBackend(
            root_dir=config.backend.root_dir,
            virtual_mode=config.backend.virtual_mode,
        )

    if config.backend.type == "local_shell":
        if not config.backend.local_shell:
            raise ValueError("backend.local_shell config is required when backend.type=local_shell")

        local_shell = config.backend.local_shell
        if local_shell.python and not local_shell.python.exists():
            print(
                "[local_shell] configured Python interpreter was not found: "
                f"{local_shell.python}. Falling back to PATH.",
                flush=True,
            )
            local_shell = replace(local_shell, python=None)

        return create_confirmed_local_shell_backend(config.backend.root_dir, local_shell)

    if config.backend.type == "remote":
        provider = config.backend.remote.provider if config.backend.remote else None
        raise NotImplementedError(
            "backend.type=remote is reserved for a future remote sandbox integration "
            f"and is not enabled yet (provider={provider!r})."
        )

    raise ValueError(f"Unsupported backend.type: {config.backend.type}")


@lru_cache(maxsize=8)
def _create_content_writer_cached(config_path_key: str):
    """按配置文件缓存 Agent 实例。

    MemorySaver 是挂在 Agent 实例上的。若每次 API 调用都重新创建 Agent，即使
    thread_id 相同，也无法共享前一轮 checkpoint。缓存后，单进程内同一配置的
    stream_chat/chat_once 连续调用可以获得真正的多轮上下文。
    """

    config = load_main_config(Path(config_path_key))
    model = create_qwen_model(config.model)
    system_prompt = _read_system_prompt(config)

    # MemorySaver 是单进程 checkpointer：同一个 thread_id 内的多轮调用能共享上下文，
    # 但进程重启后不会恢复。这正好匹配当前第一版“控制台会话记忆”的范围。
    checkpointer = MemorySaver()

    backend = _build_backend(config)

    return create_deep_agent(
        name=config.name,
        model=model,
        system_prompt=system_prompt,
        memory=config.memory,
        skills=config.skills,
        tools=get_tools(config.tools),
        subagents=_build_subagents(config, model),
        backend=backend,
        checkpointer=checkpointer,
    )


def create_content_writer(config_path: str | Path | None = None):
    """创建内容写作主智能体。

    参数：
        config_path: 可选的 main_agent.yaml 路径。为空时读取项目默认配置。

    返回：
        已编译的 Deep Agents/LangGraph runnable，可被 invoke 或 stream 调用。
    """

    resolved_config = resolve_project_path(config_path or DEFAULT_MAIN_CONFIG).resolve()
    return _create_content_writer_cached(str(resolved_config))
