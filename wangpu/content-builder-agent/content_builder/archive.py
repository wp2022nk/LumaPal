"""Unified daily archive storage for conversations and generated artifacts.

The archive is the durable, user-facing record. LangGraph checkpoints remain
runtime state; this module stores only displayable chat messages, live progress
events, and file manifests under ``history/YYYY-MM-DD/conversations/<thread>``.
"""

from __future__ import annotations

import json
import mimetypes
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import WORKSPACE_DIR


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".htm",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".svg",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
ARCHIVE_ARTIFACT_SUFFIXES = IMAGE_SUFFIXES | {".html", ".htm", ".pdf", ".md", ".json", ".txt", ".wav", ".mp3"}


@dataclass(frozen=True)
class ArchiveConversationPaths:
    thread_id: str
    day: str
    root: Path
    chat: Path
    manifest: Path
    events: Path
    uploads: Path
    artifacts: Path
    storybooks: Path
    games: Path
    reports: Path
    workspace: Path

    def ensure(self) -> "ArchiveConversationPaths":
        for directory in (
            self.root,
            self.uploads / "images",
            self.artifacts,
            self.storybooks,
            self.games,
            self.reports,
            self.workspace,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        if not self.chat.exists():
            _atomic_write_json(self.chat, [])
        if not self.manifest.exists():
            _atomic_write_json(
                self.manifest,
                {
                    "thread_id": self.thread_id,
                    "date": self.day,
                    "updated_at": _now(),
                    "artifacts": [],
                },
            )
        return self


def history_root() -> Path:
    configured = os.environ.get("CONTENT_BUILDER_HISTORY_DIR")
    return (Path(configured) if configured else WORKSPACE_DIR / "history").resolve()


def today() -> str:
    return datetime.now().astimezone().date().isoformat()


def conversation_paths(thread_id: str, *, day: str | None = None) -> ArchiveConversationPaths:
    from .thread_storage import validate_thread_id

    safe_thread_id = validate_thread_id(thread_id)
    archive_day = day or today()
    root = history_root() / archive_day / "conversations" / safe_thread_id
    return ArchiveConversationPaths(
        thread_id=safe_thread_id,
        day=archive_day,
        root=root,
        chat=root / "chat.json",
        manifest=root / "manifest.json",
        events=root / "events.jsonl",
        uploads=root / "uploads",
        artifacts=root / "artifacts" / "files",
        storybooks=root / "artifacts" / "storybooks",
        games=root / "artifacts" / "games",
        reports=root / "artifacts" / "reports",
        workspace=root / "workspace",
    ).ensure()


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") in {"text", "output_text"} or "text" in block:
                    parts.append(str(block.get("text") or ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts).strip()
    return "" if content is None else str(content)


def normalize_chat_message(message: Any, *, source: str = "") -> dict[str, Any] | None:
    if not isinstance(message, dict):
        role = getattr(message, "type", getattr(message, "role", ""))
        content = getattr(message, "content", "")
        message_id = getattr(message, "id", None)
    else:
        role = message.get("type") or message.get("role")
        content = message.get("content")
        message_id = message.get("id")

    normalized_role = str(role or "").lower()
    if normalized_role in {"human", "user"}:
        archive_role = "user"
    elif normalized_role in {"ai", "assistant", "agent"}:
        archive_role = "agent"
    else:
        return None

    text = _text_from_content(content).strip()
    if not text:
        return None
    return {
        "id": str(message_id or f"msg-{uuid.uuid4().hex}"),
        "role": archive_role,
        "content": text,
        "created_at": _now(),
        "source": source,
    }


def append_chat_messages(thread_id: str, messages: list[Any], *, source: str = "", day: str | None = None) -> Path:
    paths = conversation_paths(thread_id, day=day)
    existing = _read_json(paths.chat, [])
    chat = existing if isinstance(existing, list) else []
    seen = {
        (str(item.get("id") or ""), str(item.get("role") or ""), str(item.get("content") or ""))
        for item in chat
        if isinstance(item, dict)
    }
    seen_display = {
        (str(item.get("role") or ""), str(item.get("content") or ""))
        for item in chat
        if isinstance(item, dict)
    }
    changed = False
    for message in messages:
        normalized = normalize_chat_message(message, source=source)
        if normalized is None:
            continue
        key = (normalized["id"], normalized["role"], normalized["content"])
        display_key = (normalized["role"], normalized["content"])
        if key in seen or display_key in seen_display:
            continue
        chat.append(normalized)
        seen.add(key)
        seen_display.add(display_key)
        changed = True
    if changed:
        _atomic_write_json(paths.chat, chat)
        rebuild_day_index(paths.day)
    return paths.chat


def replace_chat_messages(thread_id: str, messages: list[Any], *, source: str = "", day: str | None = None) -> Path:
    paths = conversation_paths(thread_id, day=day)
    chat = [message for message in (normalize_chat_message(item, source=source) for item in messages) if message]
    _atomic_write_json(paths.chat, chat)
    rebuild_day_index(paths.day)
    return paths.chat


def append_event(thread_id: str, event: dict[str, Any], *, day: str | None = None) -> Path:
    paths = conversation_paths(thread_id, day=day)
    payload = {
        "id": event.get("id") or f"evt-{uuid.uuid4().hex}",
        "recorded_at": _now(),
        "thread_id": paths.thread_id,
        **event,
    }
    paths.events.parent.mkdir(parents=True, exist_ok=True)
    with paths.events.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    rebuild_day_index(paths.day)
    return paths.events


def recent_events(thread_id: str, *, limit: int = 80) -> list[dict[str, Any]]:
    paths = conversation_paths(thread_id)
    return _recent_events_from_path(paths.events, limit=limit)


def _recent_events_from_path(path: Path, *, limit: int = 80) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows[-limit:]


def _artifact_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix in TEXT_SUFFIXES:
        return "text"
    return "download"


def _artifact_category(relative: str, path: Path) -> str:
    normalized = relative.replace("\\", "/")
    suffix = path.suffix.lower()
    if normalized.startswith("artifacts/storybooks/"):
        if suffix in {".wav", ".mp3"}:
            return "audiobook"
        return "storybook"
    if normalized.startswith("artifacts/games/"):
        return "game"
    if normalized.startswith("artifacts/reports/"):
        return "growth_report"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    return "document"


def _artifact_title(relative: str, path: Path) -> str:
    parts = relative.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0] == "artifacts":
        return parts[2] if path.name in {"index.html", "book.html"} else path.stem
    return path.stem or path.name


def _entry_from_file(paths: ArchiveConversationPaths, file_path: Path) -> dict[str, Any] | None:
    if not file_path.is_file() or file_path.suffix.lower() not in ARCHIVE_ARTIFACT_SUFFIXES:
        return None
    relative = file_path.relative_to(paths.root).as_posix()
    if relative in {"chat.json", "manifest.json", "events.jsonl"}:
        return None
    stat = file_path.stat()
    return {
        "artifact_id": f"{paths.thread_id}:{relative}",
        "name": file_path.name,
        "title": _artifact_title(relative, file_path),
        "path": relative,
        "date": paths.day,
        "thread_id": paths.thread_id,
        "source": "history",
        "category": _artifact_category(relative, file_path),
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
        "created_at": datetime.fromtimestamp(stat.st_ctime).astimezone().isoformat(),
        "updated_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
        "mime_type": mimetypes.guess_type(file_path.name)[0] or "application/octet-stream",
        "kind": _artifact_kind(file_path),
        "related_files": [],
    }


def scan_artifacts(thread_id: str, *, day: str | None = None) -> list[dict[str, Any]]:
    paths = conversation_paths(thread_id, day=day)
    entries: list[dict[str, Any]] = []
    for root in (paths.uploads, paths.artifacts.parent):
        if not root.exists():
            continue
        for file_path in root.rglob("*"):
            entry = _entry_from_file(paths, file_path)
            if entry is not None:
                entries.append(entry)
    entries.sort(key=lambda item: float(item["modified_at"]), reverse=True)
    return entries


def update_manifest(thread_id: str, *, day: str | None = None) -> Path:
    paths = conversation_paths(thread_id, day=day)
    payload = {
        "thread_id": paths.thread_id,
        "date": paths.day,
        "updated_at": _now(),
        "artifacts": scan_artifacts(thread_id, day=paths.day),
    }
    _atomic_write_json(paths.manifest, payload)
    rebuild_day_index(paths.day)
    return paths.manifest


def manifest_artifacts(thread_id: str) -> list[dict[str, Any]]:
    paths = conversation_paths(thread_id)
    update_manifest(thread_id)
    payload = _read_json(paths.manifest, {})
    artifacts = payload.get("artifacts") if isinstance(payload, dict) else []
    return artifacts if isinstance(artifacts, list) else []


def read_chat(thread_id: str) -> list[dict[str, Any]]:
    payload = _read_json(conversation_paths(thread_id).chat, [])
    return payload if isinstance(payload, list) else []


def read_state(thread_id: str) -> dict[str, Any]:
    return {
        "thread_id": thread_id,
        "chat": read_chat(thread_id),
        "artifacts": manifest_artifacts(thread_id),
        "recent_events": recent_events(thread_id),
        "todos": latest_todos(thread_id),
        "subagents": latest_subagents(thread_id),
    }


def latest_todos(thread_id: str) -> list[dict[str, Any]]:
    for event in reversed(recent_events(thread_id, limit=200)):
        if event.get("type") == "todo_updated" and isinstance(event.get("todos"), list):
            return event["todos"]
    return []


def latest_subagents(thread_id: str) -> list[dict[str, Any]]:
    subagents: dict[str, dict[str, Any]] = {}
    for event in recent_events(thread_id, limit=300):
        if event.get("type") != "subagent_updated":
            continue
        key = str(event.get("subagent_id") or event.get("source") or event.get("name") or len(subagents))
        subagents[key] = {**subagents.get(key, {}), **event}
    return list(subagents.values())


def _conversation_summary(conversation_dir: Path, day: str) -> dict[str, Any] | None:
    if not conversation_dir.is_dir():
        return None
    thread_id = conversation_dir.name
    chat_payload = _read_json(conversation_dir / "chat.json", [])
    chat = chat_payload if isinstance(chat_payload, list) else []
    manifest_payload = _read_json(conversation_dir / "manifest.json", {})
    artifacts = manifest_payload.get("artifacts") if isinstance(manifest_payload, dict) else []
    if not isinstance(artifacts, list):
        artifacts = []
    updated_at = ""
    for path in (conversation_dir / "chat.json", conversation_dir / "manifest.json", conversation_dir / "events.jsonl"):
        if path.exists():
            candidate = datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()
            updated_at = max(updated_at, candidate)
    return {
        "thread_id": thread_id,
        "date": day,
        "updated_at": updated_at or _now(),
        "message_count": len(chat),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }


def rebuild_day_index(day: str | None = None) -> Path:
    archive_day = day or today()
    day_dir = history_root() / archive_day
    conversations_dir = day_dir / "conversations"
    conversations: list[dict[str, Any]] = []
    if conversations_dir.exists():
        for conversation_dir in sorted(item for item in conversations_dir.iterdir() if item.is_dir()):
            summary = _conversation_summary(conversation_dir, archive_day)
            if summary:
                conversations.append(summary)
    conversations.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    artifacts = [artifact for conversation in conversations for artifact in conversation.get("artifacts", [])]
    payload = {
        "date": archive_day,
        "updated_at": _now(),
        "conversation_count": len(conversations),
        "artifact_count": len(artifacts),
        "conversations": conversations,
        "artifacts": artifacts,
    }
    path = day_dir / "day_index.json"
    _atomic_write_json(path, payload)
    return path


def list_days() -> list[dict[str, Any]]:
    root = history_root()
    if not root.exists():
        return []
    days: list[dict[str, Any]] = []
    for day_dir in sorted((item for item in root.iterdir() if item.is_dir() and item.name != "_legacy_archive"), reverse=True):
        index_path = day_dir / "day_index.json"
        if not index_path.exists():
            rebuild_day_index(day_dir.name)
        payload = _read_json(index_path, {})
        if isinstance(payload, dict):
            days.append(payload)
    return days


def list_day_conversations(day: str) -> list[dict[str, Any]]:
    index_path = history_root() / day / "day_index.json"
    if not index_path.exists():
        rebuild_day_index(day)
    payload = _read_json(index_path, {})
    conversations = payload.get("conversations") if isinstance(payload, dict) else []
    return conversations if isinstance(conversations, list) else []


def read_day_conversation(day: str, thread_id: str) -> dict[str, Any]:
    paths = conversation_paths(thread_id, day=day)
    return {
        "thread_id": paths.thread_id,
        "date": day,
        "chat": _read_json(paths.chat, []) or [],
        "artifacts": (_read_json(paths.manifest, {}) or {}).get("artifacts", []),
        "recent_events": _recent_events_from_path(paths.events),
    }


def list_history_artifacts(start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for day in list_days():
        date = str(day.get("date") or "")
        if start_date and date < start_date:
            continue
        if end_date and date > end_date:
            continue
        for artifact in day.get("artifacts", []):
            if isinstance(artifact, dict):
                entries.append(artifact)
    entries.sort(key=lambda item: (str(item.get("date") or ""), float(item.get("modified_at") or 0)), reverse=True)
    return entries


def resolve_archive_file(path: str) -> Path:
    normalized = str(path or "").replace("\\", "/").strip("/")
    if not normalized or normalized.startswith("../") or "/../" in f"/{normalized}/":
        raise ValueError("Invalid archive path")
    root = history_root()
    if normalized.startswith("history/"):
        normalized = normalized.removeprefix("history/")
    target = (root / normalized).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Invalid archive path")
    if not target.is_file():
        raise FileNotFoundError(normalized)
    return target
