"""配置加载模块。

这个文件只负责把 YAML 和环境变量解析成 Python 对象，不直接创建 Agent，
也不定义工具。这样后续想新增 skill、子智能体或调整模型时，优先改
main_agent.yaml / subagents.yaml，而不是改业务代码。
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# LangGraph / Deep Agents 某些版本导入 checkpoint serde 时会提示未来默认值变化。
# 这个过滤器必须放在可能触发 LangGraph 导入的第三方包之前。
warnings.filterwarnings(
    "ignore",
    message=r"The default value of `allowed_objects` will change.*",
)

import yaml
from langchain_core._api.deprecation import LangChainPendingDeprecationWarning
from langchain_qwq import ChatQwen


# 这是依赖升级提醒，不影响当前运行；屏蔽它可以避免控制台流式输出被无关警告打断。
warnings.filterwarnings(
    "ignore",
    message=r"The default value of `allowed_objects` will change.*",
    category=LangChainPendingDeprecationWarning,
)
warnings.simplefilter("ignore", LangChainPendingDeprecationWarning)


PROJECT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = PROJECT_DIR.parents[1]
DEFAULT_BACKEND_DIR = WORKSPACE_DIR
DEFAULT_OUTPUT_DIR = WORKSPACE_DIR / "output"
DEFAULT_MAIN_CONFIG = PROJECT_DIR / "main_agent.yaml"

# 保留原 demo 的本地默认值，但仍然优先使用用户在终端设置的环境变量。
# 生产环境建议把这些默认值移出代码，统一交给密钥管理或 .env 注入。
DEFAULT_QWEN_API_KEY = "sk-4022a3931c75477f95b921f7dfacea8d"
DEFAULT_TAVILY_API_KEY = "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"
DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_QWEN_TEXT_MODEL = "qwen3.6-plus"
DEFAULT_THREAD_ID = "content-builder-console"
DEFAULT_SANDBOX_PYTHON = Path(r"D:\Robort_Learn\envs\deepagents\python.exe")


@dataclass(frozen=True)
class ModelConfig:
    """主模型配置。

    YAML 中只保存模型名和环境变量名；真正的 key/base_url 在这里统一解析。
    这样工具、子智能体和主智能体复用同一个模型对象时，不需要重复读环境变量。
    """

    model: str
    api_key: str
    base_url: str


@dataclass(frozen=True)
class LocalShellConfig:
    """本地命令执行配置。

    这个配置只用于受控的本地开发环境。它会让 Agent 获得 shell 执行能力，
    因此默认要求人工确认，并且只允许白名单里的顶层命令。
    """

    python: Path | None
    allowed_commands: list[str]
    require_confirmation: bool = True


@dataclass(frozen=True)
class RemoteSandboxConfig:
    """远程沙盒配置占位。

    第一版只保留接口，不初始化任何远程 provider。
    """

    provider: str | None = None
    sandbox_id: str | None = None
    setup_script: Path | None = None


@dataclass(frozen=True)
class BackendConfig:
    """Deep Agents 文件系统后端配置。"""

    type: str
    root_dir: Path
    virtual_mode: bool = True
    local_shell: LocalShellConfig | None = None
    remote: RemoteSandboxConfig | None = None


@dataclass(frozen=True)
class ConversationConfig:
    """多轮对话配置。

    max_turns 不是 MemorySaver 自带参数。MemorySaver 只负责按 thread_id 保存
    checkpoint；轮次保留策略需要应用层在每轮调用前主动裁剪 messages state。
    None 或小于等于 0 表示不限制。
    """

    max_turns: int | None = None


@dataclass(frozen=True)
class MainAgentConfig:
    """主智能体的完整配置。

    这个 dataclass 是 YAML 和 create_deep_agent 之间的稳定中间层。
    其他模块只依赖这个对象，避免散落地读取 YAML 字段。
    """

    config_path: Path
    name: str
    model: ModelConfig
    system_prompt_file: str | None
    memory: list[str]
    skills: list[str]
    tools: list[str]
    subagents_config: Path
    backend: BackendConfig
    output_root: Path
    thread_id: str
    conversation: ConversationConfig


def _read_yaml(path: Path) -> dict[str, Any]:
    """读取 YAML 文件并保证顶层是字典。

    返回空字典会让配置错误变得隐蔽，所以这里在格式不符合预期时直接报错。
    """

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"YAML config must be a mapping: {path}")
    return data


def resolve_project_path(value: str | Path, *, base_dir: Path = PROJECT_DIR) -> Path:
    """把配置里的相对路径解析到项目目录下。

    Deep Agents 的 FilesystemBackend 使用真实 root_dir，而 memory/skills 使用
    虚拟路径。这里仅用于解析真实文件位置，例如 main_agent.yaml、AGENTS.md。
    """

    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def resolve_output_root(configured_root: str | Path | None = None) -> Path:
    """Resolve the writable workspace root used for generated artifacts."""

    env_root = os.environ.get("CONTENT_BUILDER_OUTPUT_DIR")
    raw_root = env_root or configured_root or DEFAULT_OUTPUT_DIR
    base_dir = WORKSPACE_DIR if env_root else PROJECT_DIR
    path = Path(raw_root)
    return (path if path.is_absolute() else base_dir / path).resolve()


def resolve_backend_root(configured_root: str | Path | None = None) -> Path:
    """Resolve the standard Deep Agents filesystem backend root."""

    env_root = os.environ.get("CONTENT_BUILDER_BACKEND_DIR")
    raw_root = env_root or configured_root or DEFAULT_BACKEND_DIR
    path = Path(raw_root)
    return (path if path.is_absolute() else PROJECT_DIR / path).resolve()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_main_config(config_path: str | Path | None = None) -> MainAgentConfig:
    """加载主智能体配置。

    参数：
        config_path: 可选的 main_agent.yaml 路径；为空时使用项目根目录下的默认配置。

    返回：
        MainAgentConfig，供 agent_factory 构建 Deep Agent 使用。
    """

    resolved_config_path = resolve_project_path(config_path or DEFAULT_MAIN_CONFIG)
    raw = _read_yaml(resolved_config_path)
    model_raw = raw.get("model") or {}
    backend_raw = raw.get("backend") or {}
    conversation_raw = raw.get("conversation") or {}

    model_name = os.environ.get(
        str(model_raw.get("env_model", "QWEN_TEXT_MODEL")),
        str(model_raw.get("name", DEFAULT_QWEN_TEXT_MODEL)),
    )
    api_key = os.environ.get(
        str(model_raw.get("env_api_key", "QWEN_API_KEY")),
        str(model_raw.get("api_key", DEFAULT_QWEN_API_KEY)),
    )
    base_url = os.environ.get(
        str(model_raw.get("env_base_url", "QWEN_BASE_URL")),
        str(model_raw.get("base_url", DEFAULT_QWEN_BASE_URL)),
    )

    # 老脚本依赖这些环境变量存在。这里用 setdefault 保持兼容，同时不覆盖用户显式配置。
    os.environ.setdefault("QWEN_API_KEY", api_key)
    os.environ.setdefault("TAVILY_API_KEY", os.environ.get("TAVILY_API_KEY", DEFAULT_TAVILY_API_KEY))

    backend_type = str(backend_raw.get("type", "filesystem")).strip().lower()
    if backend_type not in {"filesystem", "local_shell", "remote"}:
        raise ValueError(
            "backend.type must be one of: filesystem, local_shell, remote "
            f"(got {backend_type!r})"
        )

    root_dir = resolve_backend_root(backend_raw.get("root_dir", DEFAULT_BACKEND_DIR))
    root_dir.mkdir(parents=True, exist_ok=True)
    output_root = resolve_output_root(raw.get("output_root", DEFAULT_OUTPUT_DIR))
    output_root.mkdir(parents=True, exist_ok=True)
    subagents_config = resolve_project_path(raw.get("subagents_config", "subagents.yaml"))
    local_shell_raw = backend_raw.get("local_shell") or {}
    remote_raw = backend_raw.get("remote") or {}

    python_path_raw = os.environ.get("CONTENT_BUILDER_SANDBOX_PYTHON") or local_shell_raw.get("python")
    if not python_path_raw and DEFAULT_SANDBOX_PYTHON.exists():
        python_path_raw = DEFAULT_SANDBOX_PYTHON
    python_path = resolve_project_path(python_path_raw) if python_path_raw else None
    allowed_commands = list(
        local_shell_raw.get(
            "allowed_commands",
            ["python", "pytest", "node", "npm", "powershell", "pwsh", "mkdir"],
        )
    )

    setup_script_raw = remote_raw.get("setup_script")

    require_confirmation = _env_bool(
        "CONTENT_BUILDER_REQUIRE_EXECUTE_CONFIRMATION",
        bool(local_shell_raw.get("require_confirmation", True)),
    )

    return MainAgentConfig(
        config_path=resolved_config_path,
        name=str(raw.get("name", "content-writer")),
        model=ModelConfig(model=model_name, api_key=api_key, base_url=base_url),
        system_prompt_file=raw.get("system_prompt_file"),
        memory=list(raw.get("memory", ["/AGENTS.md"])),
        skills=list(raw.get("skills", ["/skills/"])),
        tools=list(raw.get("tools", [])),
        subagents_config=subagents_config,
        backend=BackendConfig(
            type=backend_type,
            root_dir=root_dir,
            virtual_mode=bool(backend_raw.get("virtual_mode", True)),
            local_shell=LocalShellConfig(
                python=python_path,
                allowed_commands=[str(command) for command in allowed_commands],
                require_confirmation=require_confirmation,
            ),
            remote=RemoteSandboxConfig(
                provider=remote_raw.get("provider"),
                sandbox_id=remote_raw.get("sandbox_id"),
                setup_script=resolve_project_path(setup_script_raw) if setup_script_raw else None,
            ),
        ),
        output_root=output_root,
        thread_id=str(raw.get("thread_id", DEFAULT_THREAD_ID)),
        conversation=ConversationConfig(
            max_turns=conversation_raw.get("max_turns"),
        ),
    )


def load_subagents_yaml(config_path: str | Path) -> dict[str, Any]:
    """加载子智能体 YAML。

    子智能体配置保持字典形态，便于 agent_factory 按名称逐个绑定工具和模型。
    """

    return _read_yaml(resolve_project_path(config_path))


def create_qwen_model(config: ModelConfig) -> ChatQwen:
    """根据配置创建 ChatQwen 实例。

    主智能体和配置中声明同名模型的子智能体会复用这个对象，避免每个模块各自
    维护一份模型初始化参数。
    """

    return ChatQwen(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
    )
