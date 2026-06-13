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

DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_QWEN_TEXT_MODEL = "qwen3.6-plus"
DEFAULT_THREAD_ID = "content-builder-console"
DEFAULT_SANDBOX_PYTHON = Path(r"D:\Robort_Learn\envs\deepagents\python.exe")
DEFAULT_SECRETS_FILE = PROJECT_DIR / "secrets.local.yaml"


@dataclass(frozen=True)
class ModelConfig:
    """主模型配置。

    YAML 中只保存非敏感模型参数；真正的 key 统一从 secrets.local.yaml 读取。
    这样工具、子智能体和主智能体复用同一个模型对象时，不需要重复维护密钥来源。
    """

    model: str
    api_key: str | None
    base_url: str
    enable_thinking: bool | None = None
    thinking_budget: int | None = None


@dataclass(frozen=True)
class SecretsConfig:
    """本地密钥配置。

    真实 API key 不再写死在代码里，而是集中放在 secrets.local.yaml。
    这个文件会被 git 忽略；配置对象只负责把 key 传给需要的运行时组件。
    """

    path: Path
    qwen_api_key: str | None = None
    dashscope_api_key: str | None = None
    tavily_api_key: str | None = None


@dataclass(frozen=True)
class VoiceASRConfig:
    """语音输入 ASR 配置，目前只启用本地 FunASR。"""

    provider: str
    model_dir: Path
    vad_model_dir: Path
    device: str = "cpu"
    max_single_segment_time: int = 30000
    save_audio: bool = False


@dataclass(frozen=True)
class VoiceTTSConfig:
    """语音输出 TTS 配置，目前只启用 Qwen TTS。"""

    provider: str
    model: str
    voice: str
    language_type: str
    format: str
    stream: bool
    sample_rate: int
    channels: int
    sample_width: int
    timeout: int


@dataclass(frozen=True)
class VoicePlaybackConfig:
    """控制台本地播放配置。"""

    enabled: bool


@dataclass(frozen=True)
class VoiceConfig:
    """ASR-LLM-TTS 控制台链路的统一配置。"""

    enabled: bool
    asr: VoiceASRConfig
    tts: VoiceTTSConfig
    playback: VoicePlaybackConfig


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
    secrets: SecretsConfig
    model: ModelConfig
    voice: VoiceConfig
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


def _read_yaml_if_exists(path: Path) -> dict[str, Any]:
    """读取可选 YAML 文件；文件不存在时返回空字典。

    secrets.local.yaml 是本地私密配置，首次运行前可能还不存在。这里不在
    配置加载阶段报错，而是在真正需要 key 的入口给出更清晰的中文提示。
    """

    if not path.exists():
        return {}
    return _read_yaml(path)


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


def _secret_value(raw: dict[str, Any], section: str, key: str = "api_key") -> str | None:
    """从 secrets.local.yaml 的分区中读取非空字符串。"""

    value = (raw.get(section) or {}).get(key) if isinstance(raw.get(section), dict) else None
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.startswith("填入"):
        return None
    return text


def _load_secrets(raw_config: dict[str, Any], config_path: Path) -> SecretsConfig:
    """加载本地密钥文件。

    密钥文件路径默认相对项目根目录解析，也允许在 main_agent.yaml 中通过
    secrets_file 覆盖。为了满足“统一配置文件管理 key”，这里不再提供代码内
    key 默认值，只把读取到的 key 注入环境变量兼容旧工具。
    """

    secrets_file = raw_config.get("secrets_file", DEFAULT_SECRETS_FILE)
    secrets_path = resolve_project_path(secrets_file).resolve()
    secrets_raw = _read_yaml_if_exists(secrets_path)
    qwen_api_key = _secret_value(secrets_raw, "qwen")
    dashscope_api_key = _secret_value(secrets_raw, "dashscope")
    tavily_api_key = _secret_value(secrets_raw, "tavily")

    if qwen_api_key:
        os.environ["QWEN_API_KEY"] = qwen_api_key
    if dashscope_api_key or qwen_api_key:
        os.environ["DASHSCOPE_API_KEY"] = dashscope_api_key or qwen_api_key or ""
    if tavily_api_key:
        os.environ["TAVILY_API_KEY"] = tavily_api_key

    return SecretsConfig(
        path=secrets_path,
        qwen_api_key=qwen_api_key,
        dashscope_api_key=dashscope_api_key,
        tavily_api_key=tavily_api_key,
    )


def _load_voice_config(raw_config: dict[str, Any]) -> VoiceConfig:
    """从 main_agent.yaml 读取语音链路配置。"""

    voice_raw = raw_config.get("voice") or {}
    asr_raw = voice_raw.get("asr") or {}
    tts_raw = voice_raw.get("tts") or {}
    playback_raw = voice_raw.get("playback") or {}

    return VoiceConfig(
        enabled=bool(voice_raw.get("enabled", True)),
        asr=VoiceASRConfig(
            provider=str(asr_raw.get("provider", "funasr")),
            model_dir=resolve_project_path(asr_raw.get("model_dir", "../../model/SenseVoiceSmall")).resolve(),
            vad_model_dir=resolve_project_path(
                asr_raw.get(
                    "vad_model_dir",
                    "../../model/fsmn-vad/damo/speech_fsmn_vad_zh-cn-16k-common-pytorch",
                )
            ).resolve(),
            device=str(asr_raw.get("device", "cpu")),
            max_single_segment_time=int(asr_raw.get("max_single_segment_time", 30000)),
            save_audio=bool(asr_raw.get("save_audio", False)),
        ),
        tts=VoiceTTSConfig(
            provider=str(tts_raw.get("provider", "qwen")),
            model=str(tts_raw.get("model", "qwen3-tts-flash")),
            voice=str(tts_raw.get("voice", "Cherry")),
            language_type=str(tts_raw.get("language_type", "Chinese")),
            format=str(tts_raw.get("format", "wav")),
            stream=bool(tts_raw.get("stream", True)),
            sample_rate=int(tts_raw.get("sample_rate", 24000)),
            channels=int(tts_raw.get("channels", 1)),
            sample_width=int(tts_raw.get("sample_width", 2)),
            timeout=int(tts_raw.get("timeout", 30)),
        ),
        playback=VoicePlaybackConfig(
            enabled=bool(playback_raw.get("enabled", True)),
        ),
    )


def load_main_config(config_path: str | Path | None = None) -> MainAgentConfig:
    """加载主智能体配置。

    参数：
        config_path: 可选的 main_agent.yaml 路径；为空时使用项目根目录下的默认配置。

    返回：
        MainAgentConfig，供 agent_factory 构建 Deep Agent 使用。
    """

    resolved_config_path = resolve_project_path(config_path or DEFAULT_MAIN_CONFIG)
    raw = _read_yaml(resolved_config_path)
    secrets = _load_secrets(raw, resolved_config_path)
    voice = _load_voice_config(raw)
    model_raw = raw.get("model") or {}
    backend_raw = raw.get("backend") or {}
    conversation_raw = raw.get("conversation") or {}

    model_name = os.environ.get(
        str(model_raw.get("env_model", "QWEN_TEXT_MODEL")),
        str(model_raw.get("name", DEFAULT_QWEN_TEXT_MODEL)),
    )
    api_key = secrets.qwen_api_key
    base_url = os.environ.get(
        str(model_raw.get("env_base_url", "QWEN_BASE_URL")),
        str(model_raw.get("base_url", DEFAULT_QWEN_BASE_URL)),
    )
    enable_thinking_raw = model_raw.get("enable_thinking", True)
    enable_thinking = (
        enable_thinking_raw
        if isinstance(enable_thinking_raw, bool)
        else str(enable_thinking_raw).strip().lower() in {"1", "true", "yes", "on"}
    )
    thinking_budget_raw = model_raw.get("thinking_budget")
    thinking_budget = int(thinking_budget_raw) if thinking_budget_raw not in {None, ""} else None

    # 老脚本依赖这些环境变量存在。这里仅在 secrets.local.yaml 提供 key 时注入，
    # 不再用代码内硬编码默认值兜底。
    if api_key:
        os.environ["QWEN_API_KEY"] = api_key

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
        name=str(raw.get("name", "content-builder")),
        secrets=secrets,
        model=ModelConfig(
            model=model_name,
            api_key=api_key,
            base_url=base_url,
            enable_thinking=enable_thinking,
            thinking_budget=thinking_budget,
        ),
        voice=voice,
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


def create_qwen_model(config: ModelConfig) -> Any:
    """根据配置创建 ChatQwen 实例。

    主智能体和配置中声明同名模型的子智能体会复用这个对象，避免每个模块各自
    维护一份模型初始化参数。
    """

    if not config.api_key:
        raise RuntimeError(
            "缺少 Qwen 主模型 API key。请复制 secrets.example.yaml 为 "
            "secrets.local.yaml，并填写 qwen.api_key。"
        )

    from langchain.chat_models import init_chat_model

    # Qwen/DashScope is used through its OpenAI-compatible Chat Completions
    # endpoint.  In LangChain, ``model_provider="openai"`` selects that wire
    # protocol; ``base_url`` keeps the actual provider pointed at Qwen.
    return init_chat_model(
        model=config.model,
        model_provider="openai",
        base_url=config.base_url,
        api_key=config.api_key,
        streaming=True,
        use_responses_api=False,
    )
