"""Single-child growth memory storage for report generation."""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import WORKSPACE_DIR


def memory_root() -> Path:
    configured = os.environ.get("CONTENT_BUILDER_MEMORY_DIR")
    return (Path(configured) if configured else WORKSPACE_DIR / "history" / "memory").resolve()


def profile_json_path() -> Path:
    return memory_root() / "profile.json"


def profile_markdown_path() -> Path:
    return memory_root() / "profile.md"


def events_path() -> Path:
    return memory_root() / "events.jsonl"


def default_profile() -> dict[str, Any]:
    now = datetime.now().astimezone().isoformat()
    return {
        "version": 1,
        "updated_at": now,
        "summary": "暂无稳定画像。请从孩子的提问、创作、游戏选择和亲子互动中逐步更新。",
        "interests": [],
        "personality": [],
        "favorite_topics": [],
        "storybook_style_preferences": [],
        "game_type_preferences": [],
        "expression_patterns": [],
        "parent_child_interaction": [],
        "evidence": [],
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary_path = Path(handle.name)
    temporary_path.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return default_profile()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_profile()
    if not isinstance(data, dict):
        return default_profile()
    profile = default_profile()
    profile.update(data)
    return profile


def read_profile() -> dict[str, Any]:
    return _read_json(profile_json_path())


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _merge_unique(existing: list[Any], incoming: list[Any]) -> list[Any]:
    merged = list(existing)
    seen = {json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) for item in merged}
    for item in incoming:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key not in seen:
            merged.append(item)
            seen.add(key)
    return merged


def _append_jsonl(path: Path, entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            json.dump(entry, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")


def normalize_growth_events(
    growth_events: list[Any] | None,
    *,
    thread_id: str,
    artifact_refs: list[Any] | None = None,
) -> list[dict[str, Any]]:
    now = datetime.now().astimezone().isoformat()
    normalized: list[dict[str, Any]] = []
    for raw in _as_list(growth_events):
        if isinstance(raw, dict):
            event = dict(raw)
        else:
            event = {"type": "note", "text": str(raw)}
        event.setdefault("recorded_at", now)
        event.setdefault("thread_id", thread_id)
        if artifact_refs:
            event.setdefault("artifact_refs", artifact_refs)
        normalized.append(event)
    return normalized


def render_profile_markdown(profile: dict[str, Any]) -> str:
    def lines_for(key: str) -> str:
        values = _as_list(profile.get(key))
        return "\n".join(f"- {value}" for value in values) if values else "- 暂无稳定观察"

    evidence = _as_list(profile.get("evidence"))[-8:]
    evidence_lines = "\n".join(
        f"- {item.get('date', 'unknown')}: {item.get('summary', item)}" if isinstance(item, dict) else f"- {item}"
        for item in evidence
    ) or "- 暂无证据"

    return f"""# 儿童长期画像

更新时间：{profile.get("updated_at", "")}

## 摘要
{profile.get("summary", "暂无稳定画像。")}

## 兴趣与常问主题
{lines_for("interests")}

## 性格与表达特点
{lines_for("personality")}

## 喜欢的绘本风格
{lines_for("storybook_style_preferences")}

## 偏好的游戏类型
{lines_for("game_type_preferences")}

## 表达模式
{lines_for("expression_patterns")}

## 亲子互动模式
{lines_for("parent_child_interaction")}

## 最近证据
{evidence_lines}
"""


def write_profile(profile: dict[str, Any]) -> dict[str, Any]:
    profile = deepcopy(profile)
    profile["updated_at"] = datetime.now().astimezone().isoformat()
    _atomic_write_json(profile_json_path(), profile)
    profile_markdown_path().parent.mkdir(parents=True, exist_ok=True)
    profile_markdown_path().write_text(render_profile_markdown(profile), encoding="utf-8")
    return profile


def merge_profile_updates(profile_updates: Any, *, thread_id: str) -> dict[str, Any]:
    profile = read_profile()
    updates = profile_updates if isinstance(profile_updates, dict) else {}
    merge_keys = [
        "interests",
        "personality",
        "favorite_topics",
        "storybook_style_preferences",
        "game_type_preferences",
        "expression_patterns",
        "parent_child_interaction",
    ]
    if isinstance(updates.get("summary"), str) and updates["summary"].strip():
        profile["summary"] = updates["summary"].strip()
    for key in merge_keys:
        profile[key] = _merge_unique(_as_list(profile.get(key)), _as_list(updates.get(key)))

    evidence_items = _as_list(updates.get("evidence"))
    if evidence_items:
        stamped = []
        today = datetime.now().astimezone().date().isoformat()
        for item in evidence_items:
            if isinstance(item, dict):
                stamped_item = dict(item)
            else:
                stamped_item = {"summary": str(item)}
            stamped_item.setdefault("date", today)
            stamped_item.setdefault("thread_id", thread_id)
            stamped.append(stamped_item)
        profile["evidence"] = _merge_unique(_as_list(profile.get("evidence")), stamped)

    return write_profile(profile)


def update_memory_from_snapshot(
    *,
    thread_id: str,
    growth_events: list[Any] | None = None,
    artifact_refs: list[Any] | None = None,
    profile_updates: Any = None,
) -> dict[str, Any] | None:
    normalized_events = normalize_growth_events(growth_events, thread_id=thread_id, artifact_refs=artifact_refs)
    _append_jsonl(events_path(), normalized_events)
    if profile_updates:
        return merge_profile_updates(profile_updates, thread_id=thread_id)
    if not profile_json_path().exists():
        return write_profile(default_profile())
    return None
