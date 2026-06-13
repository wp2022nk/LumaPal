#!/usr/bin/env python3
"""Move legacy content-builder history into ``history/_legacy_archive``.

Dry-run is the default. Pass ``--apply`` to move files. The new archive layout
under ``history/YYYY-MM-DD/conversations`` is never moved by this script.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = PROJECT_DIR.parents[1]
HISTORY_ROOT = Path(os.environ.get("CONTENT_BUILDER_HISTORY_DIR", WORKSPACE_DIR / "history")).resolve()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_relative(path: Path, root: Path) -> str:
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Refusing to archive path outside workspace: {path}")
    return resolved.relative_to(root).as_posix()


def _legacy_history_children() -> list[Path]:
    if not HISTORY_ROOT.exists():
        return []
    children: list[Path] = []
    for child in HISTORY_ROOT.iterdir():
        if child.name in {"_legacy_archive", "memory"}:
            continue
        if child.is_file():
            children.append(child)
            continue
        if not child.is_dir():
            continue
        if (child / "conversations").is_dir():
            continue
        children.append(child)
    return children


def _candidates() -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    for child in _legacy_history_children():
        candidates.append((child, f"history/{child.name}"))

    output_threads = (WORKSPACE_DIR / "output" / "threads").resolve()
    if output_threads.exists():
        candidates.append((output_threads, "output/threads"))

    roadshow = (WORKSPACE_DIR / "roadshow-final-products").resolve()
    if roadshow.exists():
        candidates.append((roadshow, "roadshow-final-products"))
    return candidates


def _move_or_report(source: Path, destination: Path, *, apply: bool) -> dict[str, Any]:
    source = source.resolve()
    destination = destination.resolve()
    _safe_relative(source, WORKSPACE_DIR)
    if HISTORY_ROOT not in destination.parents:
        raise ValueError(f"Refusing destination outside history: {destination}")
    if destination.exists():
        raise FileExistsError(f"Destination already exists: {destination}")

    record = {
        "source": str(source),
        "destination": str(destination),
        "type": "directory" if source.is_dir() else "file",
        "applied": apply,
    }
    if apply:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Actually move legacy files")
    parser.add_argument("--timestamp", default=_timestamp(), help="Archive folder timestamp")
    args = parser.parse_args()

    archive_dir = HISTORY_ROOT / "_legacy_archive" / args.timestamp
    records: list[dict[str, Any]] = []
    for source, label in _candidates():
        destination = archive_dir / label
        records.append(_move_or_report(source, destination, apply=args.apply))

    index = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "applied": args.apply,
        "archive_dir": str(archive_dir),
        "items": records,
    }
    print(json.dumps(index, ensure_ascii=False, indent=2))

    if args.apply:
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "legacy_index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
