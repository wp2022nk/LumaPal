"""Agent 构建模块。

这个文件把 YAML 配置映射到 create_deep_agent() 的参数。它不负责终端打印，
也不直接处理用户输入；这些职责分别放在 streaming.py 和 chat.py 中。
"""

from __future__ import annotations

import os
import warnings
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any

warnings.filterwarnings(
    "ignore",
    message=r"The default value of `allowed_objects` will change.*",
)

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend
from langgraph.checkpoint.memory import MemorySaver

from .config import (
    DEFAULT_MAIN_CONFIG,
    MAIN_CONFIG_ENV,
    PROJECT_DIR,
    LocalShellConfig,
    MainAgentConfig,
    create_qwen_model,
    load_main_config,
    load_subagents_yaml,
    resolve_project_path,
)
from .local_shell_backend import create_confirmed_local_shell_backend
from .growth_memory import memory_root
from .thread_storage import runtime_thread_id, thread_paths
from .tools import get_tools


def _read_system_prompt(config: MainAgentConfig) -> str | None:
    """读取主智能体系统提示词文件。

    Deep Agents 的 memory 会把 /AGENTS.md 暴露给 Agent 文件系统；同时传入
    system_prompt 可以让主角色设定在启动时就生效，不必等模型主动读取文件。
    """

    if not config.system_prompt_file:
        return None
    prompt_path = resolve_project_path(config.system_prompt_file)
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


def _build_backend(config: MainAgentConfig, *, require_confirmation: bool | None = None) -> Any:
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
        if require_confirmation is not None:
            local_shell = LocalShellConfig(
                python=local_shell.python,
                allowed_commands=local_shell.allowed_commands,
                require_confirmation=require_confirmation,
            )
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


def _build_server_backend(config: MainAgentConfig) -> Any:
    """Build a thread-scoped backend factory for Agent Server requests."""

    if config.backend.type != "local_shell" or not config.backend.local_shell:
        return _build_backend(config, require_confirmation=False)

    local_shell = replace(config.backend.local_shell, require_confirmation=False)
    if local_shell.python and not local_shell.python.exists():
        local_shell = replace(local_shell, python=None)

    def backend_factory(runtime: Any) -> Any:
        thread_id = runtime_thread_id(runtime)
        paths = thread_paths(thread_id, output_root=config.output_root)
        memory_dir = memory_root()
        memory_dir.mkdir(parents=True, exist_ok=True)
        path_aliases = {
            "/output/storybooks": paths.storybooks,
            "/output/games": paths.games,
            "/output/reports": paths.reports,
            "/output/growth-report": paths.reports / "growth-report",
            "/output": paths.artifacts,
            "/storybooks": paths.storybooks,
            "/games": paths.games,
            "/reports": paths.reports,
            "/uploads": paths.uploads,
            "/workspace": paths.workspace,
            "/memory": memory_dir,
            "/project": PROJECT_DIR,
        }
        shell_backend = create_confirmed_local_shell_backend(
            paths.workspace,
            local_shell,
            path_aliases=path_aliases,
            extra_env={
                "CONTENT_BUILDER_OUTPUT_DIR": str(paths.artifacts),
                "CONTENT_BUILDER_STORYBOOKS_DIR": str(paths.storybooks),
                "CONTENT_BUILDER_GAMES_DIR": str(paths.games),
                "CONTENT_BUILDER_REPORTS_DIR": str(paths.reports),
                "CONTENT_BUILDER_UPLOADS_DIR": str(paths.uploads),
                "CONTENT_BUILDER_WORKSPACE_DIR": str(paths.workspace),
                "CONTENT_BUILDER_ARCHIVE_DIR": str(paths.root),
                "CONTENT_BUILDER_MEMORY_DIR": str(memory_dir),
                "CONTENT_BUILDER_PROJECT_DIR": str(PROJECT_DIR),
                "CONTENT_BUILDER_THREAD_ID": thread_id,
            },
        )
        return CompositeBackend(
            default=shell_backend,
            routes={
                "/output/storybooks/": FilesystemBackend(root_dir=paths.storybooks, virtual_mode=True),
                "/output/games/": FilesystemBackend(root_dir=paths.games, virtual_mode=True),
                "/output/reports/": FilesystemBackend(root_dir=paths.reports, virtual_mode=True),
                "/output/growth-report/": FilesystemBackend(root_dir=paths.reports / "growth-report", virtual_mode=True),
                "/output/": FilesystemBackend(root_dir=paths.artifacts, virtual_mode=True),
                "/storybooks/": FilesystemBackend(root_dir=paths.storybooks, virtual_mode=True),
                "/games/": FilesystemBackend(root_dir=paths.games, virtual_mode=True),
                "/reports/": FilesystemBackend(root_dir=paths.reports, virtual_mode=True),
                "/uploads/": FilesystemBackend(root_dir=paths.uploads, virtual_mode=True),
                "/workspace/": FilesystemBackend(root_dir=paths.workspace, virtual_mode=True),
                "/memory/": FilesystemBackend(root_dir=memory_dir, virtual_mode=True),
                "/project/": FilesystemBackend(root_dir=PROJECT_DIR, virtual_mode=True),
            },
        )

    return backend_factory


SERVER_PERMISSIONS = [
    FilesystemPermission(
        operations=["read"],
        paths=["/project/secrets.local.yaml", "/project/*.local.yaml"],
        mode="deny",
    ),
    FilesystemPermission(operations=["write"], paths=["/project/**"], mode="deny"),
]


@lru_cache(maxsize=16)
def _create_content_writer_cached(config_path_key: str, runtime_mode: str):
    """按配置文件缓存 Agent 实例。

    MemorySaver 是挂在 Agent 实例上的。若每次 API 调用都重新创建 Agent，即使
    thread_id 相同，也无法共享前一轮 checkpoint。缓存后，单进程内同一配置的
    stream_chat/chat_once 连续调用可以获得真正的多轮上下文。
    """

    config = load_main_config(Path(config_path_key))
    model = create_qwen_model(config.model)
    system_prompt = _read_system_prompt(config)

    server_mode = runtime_mode in {"server", "web"}
    backend = _build_server_backend(config) if server_mode else _build_backend(config)

    agent_kwargs = dict(
        name=config.name,
        model=model,
        system_prompt=system_prompt,
        memory=["/project/AGENTS.md"] if server_mode else config.memory,
        skills=["/project/skills/"] if server_mode else config.skills,
        tools=get_tools(config.tools),
        subagents=_build_subagents(config, model),
        backend=backend,
    )
    if server_mode:
        agent_kwargs["permissions"] = SERVER_PERMISSIONS

    # 始终附加一个进程内 MemorySaver。
    # 设计上 LangGraph Agent Server 可以自带持久化，但 Xiaozhi 硬件链路 (turn_service)
    # 和本地 CLI 都直接在当前进程里调用 agent，并没有外部 server 帮忙保存 checkpoint。
    # 不挂 checkpointer 会导致 thread_id 形同虚设，每轮对话都从空 messages state 开始。
    # 进程内 MemorySaver 已经足够支撑"同一会话内多轮上下文"的需求。
    agent_kwargs["checkpointer"] = MemorySaver()

    return create_deep_agent(**agent_kwargs)


def create_content_writer(config_path: str | Path | None = None, *, runtime_mode: str = "cli"):
    """创建内容写作主智能体。

    参数：
        config_path: 可选的 main_agent.yaml 路径。为空时读取项目默认配置。

    返回：
        已编译的 Deep Agents/LangGraph runnable，可被 invoke 或 stream 调用。
    """

    if runtime_mode not in {"cli", "server", "web"}:
        raise ValueError("runtime_mode must be 'cli', 'server', or 'web'")

    configured_path = config_path or os.environ.get(MAIN_CONFIG_ENV) or DEFAULT_MAIN_CONFIG
    resolved_config = resolve_project_path(configured_path).resolve()
    return _create_content_writer_cached(str(resolved_config), runtime_mode)


def clear_content_writer_cache() -> None:
    """Drop cached agents after local settings change."""

    _create_content_writer_cached.cache_clear()
