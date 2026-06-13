"""Compatibility wrappers for the unified archive.

New code should import :mod:`content_builder.archive` directly.  These
functions keep older server call sites stable while writing only to the new
``history/YYYY-MM-DD/conversations/<thread_id>`` layout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import archive
from .growth_memory import update_memory_from_snapshot
from .thread_storage import validate_thread_id


def history_root() -> Path:
    return archive.history_root()


def _today() -> str:
    return archive.today()


def save_thread_daily_messages(
    thread_id: str,
    messages: list[Any],
    *,
    metadata: dict[str, Any] | None = None,
    event: dict[str, Any] | None = None,
) -> Path:
    """Append displayable user/agent messages to the current conversation."""

    safe_thread_id = validate_thread_id(thread_id)
    source = str((metadata or {}).get("source") or "")
    day = _today()
    path = archive.append_chat_messages(safe_thread_id, messages, source=source, day=day)
    if event is not None:
        archive.append_event(safe_thread_id, event, day=day)
    archive.update_manifest(safe_thread_id, day=day)
    archive.rebuild_day_index(day)
    return path


def save_thread_history_snapshot(
    thread_id: str,
    *,
    messages: list[Any] | None = None,
    mode: str = "replace",
    metadata: dict[str, Any] | None = None,
    event: dict[str, Any] | None = None,
    growth_events: list[Any] | None = None,
    artifact_refs: list[Any] | None = None,
    profile_updates: dict[str, Any] | None = None,
) -> Path:
    """Persist chat, progress events, and artifact indexes in the archive."""

    safe_thread_id = validate_thread_id(thread_id)
    source = str((metadata or {}).get("source") or "")
    day = _today()
    path = archive.conversation_paths(safe_thread_id, day=day).chat
    if messages is not None:
        if mode == "append":
            path = archive.append_chat_messages(safe_thread_id, messages, source=source, day=day)
        else:
            path = archive.replace_chat_messages(safe_thread_id, messages, source=source, day=day)

    if event is not None:
        archive.append_event(safe_thread_id, event, day=day)
    for item in growth_events or []:
        payload = item if isinstance(item, dict) else {"text": str(item)}
        growth_type = payload.get("type")
        archive.append_event(
            safe_thread_id,
            {
                **payload,
                "type": "growth_event",
                "growth_type": growth_type,
            },
            day=day,
        )
    for item in artifact_refs or []:
        payload = item if isinstance(item, dict) else {"path": str(item)}
        artifact_type = payload.get("type")
        archive.append_event(
            safe_thread_id,
            {
                **payload,
                "type": "artifact_ref",
                "artifact_type": artifact_type,
            },
            day=day,
        )
    if profile_updates:
        archive.append_event(safe_thread_id, {"type": "profile_update", "updates": profile_updates}, day=day)
    if growth_events or profile_updates:
        update_memory_from_snapshot(
            thread_id=safe_thread_id,
            growth_events=growth_events,
            artifact_refs=artifact_refs,
            profile_updates=profile_updates,
        )

    archive.update_manifest(safe_thread_id, day=day)
    archive.rebuild_day_index(day)
    return path
