"""Custom HTTP routes served beside the LangGraph Agent Server."""

from __future__ import annotations

import base64
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from content_builder.config import load_main_config
from content_builder.manifest import ARTIFACT_ROOTS, artifact_kind, build_manifest


app = FastAPI(title="Content Builder Agent API")

LOCAL_DEV_ORIGINS = [
    f"http://localhost:{port}" for port in range(5173, 5181)
] + [
    f"http://127.0.0.1:{port}" for port in range(5173, 5181)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=LOCAL_DEV_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

IGNORED_DIRS = {
    ".git",
    ".langgraph_api",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
}

TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".vue",
    ".json",
    ".yaml",
    ".yml",
    ".css",
    ".html",
    ".csv",
    ".toml",
}


def _workspace_root() -> Path:
    root = load_main_config().output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _relative_display(path: Path, root: Path) -> str:
    if path == root:
        return "."
    return str(path.relative_to(root)).replace("\\", "/")


def _resolve_workspace_path(raw_path: str | None, root: Path) -> Path:
    cleaned = (raw_path or ".").strip().strip("\"'")
    if cleaned in {"", ".", "/"}:
        return root
    cleaned = cleaned.replace("\\", "/").lstrip("/")
    resolved = (root / cleaned).resolve()
    if resolved != root and root not in resolved.parents:
        raise HTTPException(status_code=400, detail="Path escapes workspace root")
    return resolved


def _is_ignored(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part in IGNORED_DIRS for part in parts)


def _entry(path: Path, root: Path) -> dict[str, Any]:
    stat = path.stat()
    mime_type, _ = mimetypes.guess_type(path.name)
    return {
        "name": path.name or ".",
        "path": _relative_display(path, root),
        "type": "directory" if path.is_dir() else "file",
        "kind": "directory" if path.is_dir() else artifact_kind(path),
        "mime": mime_type or "application/octet-stream",
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


@app.get("/api/agent/manifest")
def agent_manifest() -> dict[str, Any]:
    return build_manifest()


@app.get("/api/workspace/tree")
def workspace_tree(
    path: str = Query(".", description="Workspace-relative path"),
    depth: int = Query(1, ge=1, le=3),
) -> dict[str, Any]:
    workspace_root = _workspace_root()
    root = _resolve_workspace_path(path, workspace_root)
    if not root.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not root.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    entries: list[dict[str, Any]] = []
    queue: list[tuple[Path, int]] = [(root, 0)]
    while queue:
        current, level = queue.pop(0)
        for child in sorted(current.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if _is_ignored(child, workspace_root):
                continue
            entries.append(_entry(child, workspace_root))
            if child.is_dir() and level + 1 < depth:
                queue.append((child, level + 1))

    return {"path": _relative_display(root, workspace_root), "entries": entries}


@app.get("/api/workspace/file")
def workspace_file(path: str = Query(..., description="Workspace-relative file path")) -> dict[str, Any]:
    workspace_root = _workspace_root()
    resolved = _resolve_workspace_path(path, workspace_root)
    if not resolved.exists() or not resolved.is_file() or _is_ignored(resolved, workspace_root):
        raise HTTPException(status_code=404, detail="File not found")

    mime_type, _ = mimetypes.guess_type(resolved.name)
    size = resolved.stat().st_size
    if resolved.suffix.lower() in TEXT_SUFFIXES or (mime_type and mime_type.startswith("text/")):
        return {
            "path": _relative_display(resolved, workspace_root),
            "mime": mime_type or "text/plain",
            "encoding": "text",
            "content": resolved.read_text(encoding="utf-8", errors="replace"),
            "size": size,
        }

    return {
        "path": _relative_display(resolved, workspace_root),
        "mime": mime_type or "application/octet-stream",
        "encoding": "base64",
        "content": base64.b64encode(resolved.read_bytes()).decode("ascii"),
        "size": size,
    }


@app.get("/api/workspace/asset")
def workspace_asset(path: str = Query(..., description="Workspace-relative asset path")) -> FileResponse:
    workspace_root = _workspace_root()
    resolved = _resolve_workspace_path(path, workspace_root)
    if not resolved.exists() or not resolved.is_file() or _is_ignored(resolved, workspace_root):
        raise HTTPException(status_code=404, detail="Asset not found")
    return FileResponse(resolved)


@app.get("/api/artifacts")
def artifacts(limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    workspace_root = _workspace_root()
    files: list[Path] = []
    for root_name in ARTIFACT_ROOTS:
        root = workspace_root / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and not _is_ignored(path, workspace_root):
                files.append(path)

    files.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    items = []
    for path in files[:limit]:
        item = _entry(path, workspace_root)
        item["url"] = f"/api/workspace/asset?path={quote(item['path'])}"
        items.append(item)
    return {"items": items}
