"""Daily sidecar history snapshots for LAN app conversations."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import WORKSPACE_DIR
from .growth_memory import update_memory_from_snapshot
from .thread_storage import thread_paths, validate_thread_id


def history_root() -> Path:
    configured = os.environ.get("CONTENT_BUILDER_HISTORY_DIR")
    return (Path(configured) if configured else WORKSPACE_DIR / "history").resolve()


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _snapshot_path(*, day: str | None = None) -> Path:
    return history_root() / (day or _today()) / "history.json"


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        target = destination / item.relative_to(source)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)


def _file_entries(root: Path, namespace: str, *, thread_id: str | None = None) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    entries: list[dict[str, Any]] = []
    for item in root.rglob("*"):
        if item.is_file():
            entry = {
                "path": f"{namespace}/{item.relative_to(root).as_posix()}",
                "size": item.stat().st_size,
                "modified_at": item.stat().st_mtime,
            }
            if thread_id is not None:
                entry["thread_id"] = thread_id
            entries.append(entry)
    return sorted(entries, key=lambda value: str(value["path"]))


def _read_previous(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def save_thread_history_snapshot(
    thread_id: str,
    *,
    messages: list[Any] | None = None,
    metadata: dict[str, Any] | None = None,
    event: dict[str, Any] | None = None,
    growth_events: list[Any] | None = None,
    artifact_refs: list[Any] | None = None,
    profile_updates: dict[str, Any] | None = None,
) -> Path:
    """Save a JSON chat snapshot and mirror the day's generated files."""

    safe_thread_id = validate_thread_id(thread_id)
    day = _today()
    path = _snapshot_path(day=day)
    previous = _read_previous(path)
    paths = thread_paths(safe_thread_id)
    day_dir = path.parent

    for name, source in (
        ("uploads", paths.uploads),
        ("artifacts", paths.artifacts),
        ("games", paths.games),
    ):
        _copy_tree(source, day_dir / name)

    conversations = previous.get("conversations")
    if not isinstance(conversations, dict):
        conversations = {}
    previous_conversation = conversations.get(safe_thread_id)
    if not isinstance(previous_conversation, dict):
        previous_conversation = {}
    conversations[safe_thread_id] = {
        "thread_id": safe_thread_id,
        "updated_at": datetime.now().astimezone().isoformat(),
        "messages": messages if messages is not None else previous_conversation.get("messages", []),
        "metadata": metadata if metadata is not None else previous_conversation.get("metadata", {}),
        "growth_events": growth_events if growth_events is not None else previous_conversation.get("growth_events", []),
        "artifact_refs": artifact_refs if artifact_refs is not None else previous_conversation.get("artifact_refs", []),
        "profile_updates": profile_updates
        if profile_updates is not None
        else previous_conversation.get("profile_updates", {}),
    }

    events = list(previous.get("events") if isinstance(previous.get("events"), list) else [])
    if event is not None:
        events.append({"recorded_at": datetime.now().astimezone().isoformat(), "thread_id": safe_thread_id, **event})

    previous_growth_events = list(previous.get("growth_events") if isinstance(previous.get("growth_events"), list) else [])
    if growth_events:
        now = datetime.now().astimezone().isoformat()
        previous_growth_events.extend(
            [
                {
                    "recorded_at": now,
                    "thread_id": safe_thread_id,
                    **(item if isinstance(item, dict) else {"type": "note", "text": str(item)}),
                }
                for item in growth_events
            ]
        )

    previous_artifact_refs = list(previous.get("artifact_refs") if isinstance(previous.get("artifact_refs"), list) else [])
    if artifact_refs:
        previous_artifact_refs.extend(
            [
                {"thread_id": safe_thread_id, **item} if isinstance(item, dict) else {"thread_id": safe_thread_id, "path": str(item)}
                for item in artifact_refs
            ]
        )

    previous_profile_updates = list(
        previous.get("profile_updates") if isinstance(previous.get("profile_updates"), list) else []
    )
    if profile_updates:
        previous_profile_updates.append(
            {
                "recorded_at": datetime.now().astimezone().isoformat(),
                "thread_id": safe_thread_id,
                "updates": profile_updates,
            }
        )

    if growth_events or profile_updates:
        update_memory_from_snapshot(
            thread_id=safe_thread_id,
            growth_events=growth_events,
            artifact_refs=artifact_refs,
            profile_updates=profile_updates,
        )

    payload = {
        "date": day,
        "updated_at": datetime.now().astimezone().isoformat(),
        "conversations": conversations,
        "events": events,
        "growth_events": previous_growth_events,
        "artifact_refs": previous_artifact_refs,
        "profile_updates": previous_profile_updates,
        "files": (
            _file_entries(day_dir / "uploads", "uploads")
            + _file_entries(day_dir / "artifacts", "artifacts")
            + _file_entries(day_dir / "games", "games")
        ),
        "mirrored_dir": str(day_dir),
    }
    _atomic_write_json(path, payload)
    return path
