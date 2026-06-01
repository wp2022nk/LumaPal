"""Thread-scoped filesystem layout for the LAN Agent Server."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import load_main_config


THREAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")


@dataclass(frozen=True)
class ThreadPaths:
    """Physical directories reserved for one LangGraph conversation."""

    thread_id: str
    root: Path
    artifacts: Path
    games: Path
    uploads: Path
    workspace: Path

    def ensure(self) -> "ThreadPaths":
        for directory in (self.artifacts, self.games, self.uploads, self.workspace):
            directory.mkdir(parents=True, exist_ok=True)
        return self


def validate_thread_id(thread_id: str) -> str:
    """Validate a thread ID before using it as a physical directory name."""

    normalized = str(thread_id or "").strip()
    if not THREAD_ID_PATTERN.fullmatch(normalized):
        raise ValueError("thread_id must contain only safe URL and filesystem characters")
    return normalized


def thread_paths(thread_id: str, *, output_root: Path | None = None) -> ThreadPaths:
    """Return the isolated storage layout for a LangGraph thread."""

    safe_thread_id = validate_thread_id(thread_id)
    root = (output_root or load_main_config().output_root).resolve() / "threads" / safe_thread_id
    return ThreadPaths(
        thread_id=safe_thread_id,
        root=root,
        artifacts=root / "artifacts",
        games=root / "games",
        uploads=root / "uploads",
        workspace=root / "workspace",
    ).ensure()


def runtime_thread_id(runtime: Any) -> str:
    """Read the stable thread identifier from an injected LangGraph runtime."""

    execution_info = getattr(runtime, "execution_info", None)
    thread_id = getattr(execution_info, "thread_id", None)
    if thread_id:
        return validate_thread_id(thread_id)

    config = getattr(runtime, "config", {}) or {}
    configurable = config.get("configurable", {}) or {}
    thread_id = configurable.get("thread_id")
    if thread_id:
        return validate_thread_id(thread_id)

    raise ValueError("A LangGraph thread_id is required for server-side artifacts")


def resolve_thread_file(thread_id: str, relative_path: str, *, output_root: Path | None = None) -> Path:
    """Resolve a thread-relative path while blocking traversal and sibling access."""

    paths = thread_paths(thread_id, output_root=output_root)
    raw = str(relative_path or "").replace("\\", "/").lstrip("/")
    if not raw:
        raise ValueError("A file path is required")
    target = (paths.root / raw).resolve()
    if paths.root != target and paths.root not in target.parents:
        raise ValueError("Path escapes the selected thread")
    return target

