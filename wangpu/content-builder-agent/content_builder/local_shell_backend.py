"""Local shell backend helpers.

This module keeps host command execution behind a small guard layer. The
underlying Deep Agents backend is still ``LocalShellBackend``; the wrapper only
adds command allow-list checks, a few obvious-danger blocks, Python venv
normalization, and an interactive approval prompt for local CLI usage.
"""

from __future__ import annotations

import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
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


def _resolve_virtual_path(
    root_dir: Path,
    raw_path: str,
    path_aliases: dict[str, Path] | None = None,
) -> Path:
    """Resolve Deep Agents-style paths into the configured root directory."""

    cleaned = raw_path.strip("\"'")
    if not cleaned:
        raise ValueError("empty path")

    normalized = cleaned.replace("\\", "/")
    for virtual_path, physical_path in sorted((path_aliases or {}).items(), reverse=True):
        virtual_root = virtual_path.rstrip("/")
        if normalized == virtual_root or normalized.startswith(f"{virtual_root}/"):
            relative = normalized.removeprefix(virtual_root).lstrip("/")
            alias_root = physical_path.resolve()
            resolved = (alias_root / relative).resolve()
            if resolved != alias_root and alias_root not in resolved.parents:
                raise ValueError(f"path escapes aliased root: {raw_path}")
            return resolved

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


def _handle_mkdir(
    command: str,
    root_dir: Path,
    path_aliases: dict[str, Path] | None = None,
) -> LocalExecuteResult | None:
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
            resolved = _resolve_virtual_path(root_dir, target, path_aliases)
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


def _rewrite_path_aliases(command: str, path_aliases: dict[str, Path] | None) -> str:
    """Translate virtual artifact paths before delegating to the host shell."""

    rewritten = command
    for virtual_path, physical_path in sorted((path_aliases or {}).items(), reverse=True):
        virtual_root = virtual_path.rstrip("/")
        physical_root = physical_path.resolve().as_posix()
        rewritten = rewritten.replace(f"{virtual_root}/", f"{physical_root}/")
        if rewritten.endswith(virtual_root):
            rewritten = f"{rewritten[:-len(virtual_root)]}{physical_root}"
    return rewritten


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


def _build_env(config: LocalShellConfig, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
    }
    if config.python:
        scripts_dir = str(config.python.parent)
        env["PATH"] = scripts_dir + os.pathsep + env["PATH"]
        env["VIRTUAL_ENV"] = str(config.python.parent.parent)
    env.update(extra_env or {})
    return env


def _dispatch_sandbox_output(data: dict[str, Any]) -> None:
    """Emit a best-effort LangGraph custom event for live shell output."""

    try:
        from langchain_core.callbacks.manager import dispatch_custom_event

        dispatch_custom_event("content_builder.sandbox_output", data)
    except RuntimeError:
        # CLI and tests may call execute outside a runnable callback context.
        return
    except Exception:
        return


def create_confirmed_local_shell_backend(
    root_dir: Path,
    config: LocalShellConfig,
    *,
    path_aliases: dict[str, Path] | None = None,
    extra_env: dict[str, str] | None = None,
) -> Any:
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
        def _execute_streaming(self, command: str, *, timeout: int | None = None) -> LocalExecuteResult:
            if not command or not isinstance(command, str):
                return LocalExecuteResult(
                    output="Error: Command must be a non-empty string.",
                    exit_code=1,
                    truncated=False,
                )

            effective_timeout = timeout if timeout is not None else self._default_timeout
            if effective_timeout <= 0:
                raise ValueError(f"timeout must be positive, got {effective_timeout}")

            execution_id = f"execute-{uuid.uuid4().hex[:10]}"
            _dispatch_sandbox_output(
                {
                    "type": "sandbox_output",
                    "event": "start",
                    "run_id": execution_id,
                    "command": command,
                    "chunk": "",
                }
            )

            try:
                process = subprocess.Popen(  # noqa: S602
                    command,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    bufsize=0,
                    env=self._env,
                    cwd=str(self.cwd),
                    errors="replace",
                )
            except Exception as exc:
                message = f"Error executing command ({type(exc).__name__}): {exc}"
                _dispatch_sandbox_output(
                    {
                        "type": "sandbox_output",
                        "event": "error",
                        "run_id": execution_id,
                        "command": command,
                        "chunk": message,
                        "exit_code": 1,
                    }
                )
                return LocalExecuteResult(output=message, exit_code=1, truncated=False)

            output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()

            def pump(pipe: Any, stream_name: str) -> None:
                try:
                    while True:
                        chunk = pipe.read(1)
                        if chunk == "":
                            break
                        output_queue.put((stream_name, chunk))
                finally:
                    output_queue.put((stream_name, None))
                    try:
                        pipe.close()
                    except Exception:
                        pass

            streams_open = 0
            for stream_name, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
                if pipe is None:
                    continue
                streams_open += 1
                threading.Thread(target=pump, args=(pipe, stream_name), daemon=True).start()

            started_at = time.monotonic()
            output_parts: list[str] = []
            output_size = 0
            truncated = False
            timed_out = False

            while streams_open > 0:
                if time.monotonic() - started_at > effective_timeout:
                    timed_out = True
                    process.kill()
                    break

                try:
                    stream_name, chunk = output_queue.get(timeout=0.05)
                except queue.Empty:
                    continue

                if chunk is None:
                    streams_open -= 1
                    continue

                if output_size < self._max_output_bytes:
                    remaining = self._max_output_bytes - output_size
                    visible_chunk = chunk[:remaining]
                    output_parts.append(visible_chunk)
                    output_size += len(visible_chunk)
                    if visible_chunk:
                        _dispatch_sandbox_output(
                            {
                                "type": "sandbox_output",
                                "event": "chunk",
                                "run_id": execution_id,
                                "command": command,
                                "stream": stream_name,
                                "chunk": visible_chunk,
                            }
                        )
                    if len(chunk) > remaining:
                        truncated = True
                else:
                    truncated = True

            return_code = process.wait()
            if timed_out:
                output = (
                    f"Error: Command timed out after {effective_timeout} seconds. "
                    "For long-running commands, re-run using the timeout parameter."
                )
                exit_code = 124
            else:
                output = "".join(output_parts) or "<no output>"
                exit_code = return_code
                if truncated:
                    output += f"\n\n... Output truncated at {self._max_output_bytes} bytes."
                if exit_code != 0:
                    output = f"{output.rstrip()}\n\nExit code: {exit_code}"

            _dispatch_sandbox_output(
                {
                    "type": "sandbox_output",
                    "event": "end" if exit_code == 0 else "error",
                    "run_id": execution_id,
                    "command": command,
                    "chunk": "" if exit_code == 0 else f"\nExit code: {exit_code}",
                    "exit_code": exit_code,
                    "truncated": truncated,
                }
            )
            return LocalExecuteResult(output=output, exit_code=exit_code, truncated=truncated)

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

            mkdir_result = _handle_mkdir(command, root_dir, path_aliases)
            if mkdir_result is not None:
                return mkdir_result

            rewritten = _rewrite_command(_rewrite_path_aliases(command, path_aliases), config)
            if config.require_confirmation and not _approval_prompt(
                command,
                rewritten,
                root_dir,
                command_name,
            ):
                return LocalExecuteResult(output="Command not executed: user denied execution.")

            return self._execute_streaming(rewritten, timeout=kwargs.get("timeout"))

    env = _build_env(config, extra_env)
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
