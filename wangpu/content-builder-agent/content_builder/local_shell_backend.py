"""Local shell backend helpers.

This module keeps host command execution behind a small guard layer. The
underlying Deep Agents backend is still ``LocalShellBackend``; the wrapper only
adds command allow-list checks, a few obvious-danger blocks, Python venv
normalization, and an interactive approval prompt for local CLI usage.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import LocalShellConfig


POWERSHELL_COMMANDS = {"powershell", "pwsh"}
PYTHON_COMMANDS = {"python", "python3", "py"}
DIRECTORY_COMMANDS = {"mkdir"}
DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\bremove-item\b.*\b-recurse\b",
    r"\brmdir\b.*\s/[sq]\b",
    r"\bdel\b.*\s/[sq]\b",
    r"\bformat\b",
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\breg\s+delete\b",
    r"\binvoke-expression\b",
    r"\biex\b",
    r"\bcurl\b.*\|",
    r"\biwr\b.*\|",
    r"\binvoke-webrequest\b.*\|",
]


@dataclass
class LocalExecuteResult:
    """Minimal execute response for rejected or cancelled commands."""

    output: str
    exit_code: int = 126
    error: str | None = None
    truncated: bool = False

    def __str__(self) -> str:
        status = "succeeded" if self.exit_code == 0 else "failed"
        return f"{self.output}\n[Command {status} with exit code {self.exit_code}]"


def _split_command(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return command.strip().split()


def _command_name(command: str) -> str:
    parts = _split_command(command)
    if not parts:
        return ""
    executable = parts[0].strip("\"'")
    name = Path(executable).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _quote_path(path: Path) -> str:
    return f'"{path}"'


def _resolve_virtual_path(root_dir: Path, raw_path: str) -> Path:
    """Resolve Deep Agents-style paths into the configured root directory."""

    cleaned = raw_path.strip("\"'")
    if not cleaned:
        raise ValueError("empty path")

    path = Path(cleaned)
    if cleaned.startswith(("/", "\\")) or path.is_absolute():
        relative = cleaned.lstrip("/\\")
    else:
        relative = cleaned

    resolved = (root_dir / relative).resolve()
    root = root_dir.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"path escapes backend root: {raw_path}")
    return resolved


def _handle_mkdir(command: str, root_dir: Path) -> LocalExecuteResult | None:
    """Safely handle mkdir without delegating to the host shell."""

    parts = _split_command(command)
    if not parts or _command_name(command) not in DIRECTORY_COMMANDS:
        return None

    targets = [
        part
        for part in parts[1:]
        if part not in {"-p", "--parents"} and not part.startswith("-")
    ]
    if not targets:
        return LocalExecuteResult("Command not executed: mkdir requires at least one target path.")

    created: list[str] = []
    try:
        for target in targets:
            resolved = _resolve_virtual_path(root_dir, target)
            resolved.mkdir(parents=True, exist_ok=True)
            created.append(str(resolved))
    except Exception as exc:
        return LocalExecuteResult(f"Command failed: could not create directory. {exc}", exit_code=1)

    return LocalExecuteResult(
        output="Created directory/directories:\n" + "\n".join(created),
        exit_code=0,
    )


def _rewrite_command(command: str, config: LocalShellConfig) -> str:
    """Normalize command execution for configured Python and PowerShell."""

    parts = _split_command(command)
    if not parts:
        return command

    executable = _command_name(command)
    if config.python and executable in PYTHON_COMMANDS:
        remainder = command[len(parts[0]) :].lstrip()
        return f"{_quote_path(config.python)} {remainder}".rstrip()

    if executable in POWERSHELL_COMMANDS:
        has_no_profile = any(part.strip("\"'").lower() == "-noprofile" for part in parts[1:])
        if not has_no_profile:
            remainder = command[len(parts[0]) :].lstrip()
            return f"{parts[0]} -NoProfile {remainder}".rstrip()

    return command


def _is_dangerous(command: str) -> str | None:
    normalized = command.lower()
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, normalized, flags=re.IGNORECASE):
            return pattern
    return None


def _approval_prompt(command: str, rewritten: str, root_dir: Path, command_name: str) -> bool:
    print("\n[approval] 本地命令等待确认", flush=True)
    print(f"  command: {command}", flush=True)
    if rewritten != command:
        print(f"  effective: {rewritten}", flush=True)
    print(f"  cwd: {root_dir}", flush=True)
    print(f"  category: {command_name or 'unknown'}", flush=True)
    print("  risk: 本地 shell 不是隔离沙盒，命令会以当前用户权限运行。", flush=True)

    if not sys.stdin.isatty():
        print("  decision: denied (non-interactive stdin)", flush=True)
        return False

    answer = input("允许执行这个命令吗？输入 yes 执行，其它输入取消: ").strip().lower()
    approved = answer in {"y", "yes"}
    print(f"  decision: {'approved' if approved else 'denied'}", flush=True)
    return approved


def _build_env(config: LocalShellConfig) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    if config.python:
        scripts_dir = str(config.python.parent)
        env["PATH"] = scripts_dir + os.pathsep + env["PATH"]
        env["VIRTUAL_ENV"] = str(config.python.parent.parent)
    return env


def create_confirmed_local_shell_backend(root_dir: Path, config: LocalShellConfig) -> Any:
    """Create a guarded LocalShellBackend instance.

    ``deepagents`` is imported lazily so the package can still be inspected in
    environments where the runtime dependency is installed elsewhere.
    """

    try:
        from deepagents.backends import LocalShellBackend
    except ImportError as exc:
        raise RuntimeError(
            "backend.type=local_shell requires a deepagents version that provides "
            "LocalShellBackend."
        ) from exc

    allowed_commands = {
        command.strip().lower().removesuffix(".exe")
        for command in config.allowed_commands
        if command.strip()
    }

    class ConfirmedLocalShellBackend(LocalShellBackend):  # type: ignore[misc, valid-type]
        def execute(self, command: str, *args: Any, **kwargs: Any) -> Any:
            command_name = _command_name(command)
            if command_name not in allowed_commands:
                return LocalExecuteResult(
                    output=(
                        "Command not executed: top-level command "
                        f"{command_name!r} is not in the local_shell allow-list. "
                        f"Allowed commands: {', '.join(sorted(allowed_commands))}."
                    )
                )

            matched_pattern = _is_dangerous(command)
            if matched_pattern:
                return LocalExecuteResult(
                    output=(
                        "Command not executed: command matched a blocked safety "
                        f"pattern ({matched_pattern})."
                    )
                )

            rewritten = _rewrite_command(command, config)
            if config.require_confirmation and not _approval_prompt(
                command,
                rewritten,
                root_dir,
                command_name,
            ):
                return LocalExecuteResult(output="Command not executed: user denied execution.")

            mkdir_result = _handle_mkdir(rewritten, root_dir)
            if mkdir_result is not None:
                return mkdir_result

            return super().execute(rewritten, *args, **kwargs)

    env = _build_env(config)
    try:
        return ConfirmedLocalShellBackend(
            root_dir=root_dir,
            env=env,
            inherit_env=False,
            virtual_mode=True,
        )
    except TypeError:
        try:
            return ConfirmedLocalShellBackend(
                root_dir=root_dir,
                env=env,
                virtual_mode=True,
            )
        except TypeError:
            return ConfirmedLocalShellBackend(root_dir=root_dir, env=env)
