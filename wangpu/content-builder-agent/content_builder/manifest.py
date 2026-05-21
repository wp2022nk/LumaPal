"""Runtime metadata for the web demo console.

The Vue frontend intentionally avoids hard-coded tools, skills, subagents, and
artifact types. This module gathers the current backend configuration into a
small manifest that the frontend can refresh whenever the backend changes.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import yaml

from .config import PROJECT_DIR, MainAgentConfig, load_main_config, load_subagents_yaml, resolve_project_path
from .tools import TOOL_REGISTRY, get_tools


ARTIFACT_ROOTS = [
    "blogs",
    "research",
    "story",
    "analysis",
    "linkedin",
    "tweets",
    "social",
]

MUTATING_TOOL_NAMES = {
    "write_file",
    "edit_file",
    "execute",
    "generate_cover",
    "generate_social_image",
}

BUILTIN_TOOLS = [
    {
        "name": "write_todos",
        "description": "Track and update the Deep Agents task plan.",
        "schema": {
            "type": "object",
            "properties": {"todos": {"type": "array"}},
            "required": ["todos"],
        },
        "source": "deepagents_builtin",
        "mutates_workspace": False,
    },
    {
        "name": "task",
        "description": "Delegate work to a specialist subagent.",
        "schema": {"type": "object"},
        "source": "deepagents_builtin",
        "mutates_workspace": False,
    },
    {
        "name": "execute",
        "description": "Run a local command in the configured sandbox.",
        "schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        "source": "local_shell_backend",
        "mutates_workspace": True,
        "requires_approval": False,
    },
    {
        "name": "read_file",
        "description": "Read a file from the Deep Agents filesystem backend.",
        "schema": {"type": "object"},
        "source": "deepagents_builtin",
        "mutates_workspace": False,
    },
    {
        "name": "write_file",
        "description": "Write a file to the Deep Agents filesystem backend.",
        "schema": {"type": "object"},
        "source": "deepagents_builtin",
        "mutates_workspace": True,
    },
    {
        "name": "edit_file",
        "description": "Edit an existing file in the Deep Agents filesystem backend.",
        "schema": {"type": "object"},
        "source": "deepagents_builtin",
        "mutates_workspace": True,
    },
    {
        "name": "ls",
        "description": "List files in the Deep Agents filesystem backend.",
        "schema": {"type": "object"},
        "source": "deepagents_builtin",
        "mutates_workspace": False,
    },
    {
        "name": "glob",
        "description": "Search files by glob pattern.",
        "schema": {"type": "object"},
        "source": "deepagents_builtin",
        "mutates_workspace": False,
    },
    {
        "name": "grep",
        "description": "Search file contents.",
        "schema": {"type": "object"},
        "source": "deepagents_builtin",
        "mutates_workspace": False,
    },
]


def _tool_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "args_schema", None)
    if schema is not None and hasattr(schema, "model_json_schema"):
        return schema.model_json_schema()
    args = getattr(tool, "args", None)
    if isinstance(args, dict):
        return {"type": "object", "properties": args}
    return {"type": "object"}


def _tool_metadata(name: str, tool: Any, *, configured: bool) -> dict[str, Any]:
    return {
        "name": getattr(tool, "name", name),
        "description": getattr(tool, "description", "") or "",
        "schema": _tool_schema(tool),
        "source": "configured" if configured else "registry",
        "configured": configured,
        "mutates_workspace": name in MUTATING_TOOL_NAMES,
        "requires_approval": False,
    }


def _virtual_to_real_skill_root(config: MainAgentConfig, skill_root: str) -> Path:
    if skill_root.startswith("/"):
        return config.backend.root_dir / skill_root.strip("/\\")
    return resolve_project_path(skill_root, base_dir=config.backend.root_dir)


def _parse_skill(path: Path, root: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    frontmatter: dict[str, Any] = {}
    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            frontmatter = yaml.safe_load(parts[1]) or {}
            body = parts[2]

    title = ""
    for line in body.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break

    return {
        "name": str(frontmatter.get("name") or path.parent.name),
        "description": str(frontmatter.get("description") or ""),
        "title": title or path.parent.name,
        "path": str(path.relative_to(PROJECT_DIR)).replace("\\", "/"),
        "root": str(root.relative_to(PROJECT_DIR)).replace("\\", "/") if root != PROJECT_DIR else ".",
    }


def list_skills(config: MainAgentConfig) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for skill_root in config.skills:
        real_root = _virtual_to_real_skill_root(config, skill_root)
        if not real_root.exists():
            continue
        for skill_path in sorted(real_root.glob("*/SKILL.md")):
            resolved = skill_path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            skills.append(_parse_skill(resolved, real_root.resolve()))
    return skills


def list_tools(config: MainAgentConfig) -> list[dict[str, Any]]:
    configured_names = set(config.tools)
    tools: list[dict[str, Any]] = []
    for builtin in BUILTIN_TOOLS:
        if builtin["name"] == "execute" and config.backend.type != "local_shell":
            continue
        tool = dict(builtin)
        tools.append(tool)

    for name, tool in TOOL_REGISTRY.items():
        tools.append(_tool_metadata(name, tool, configured=name in configured_names))

    return sorted(tools, key=lambda item: (item.get("source") != "configured", item["name"]))


def list_subagents(config: MainAgentConfig) -> list[dict[str, Any]]:
    raw_subagents = load_subagents_yaml(config.subagents_config)
    subagents = []
    for name, spec in raw_subagents.items():
        if not isinstance(spec, dict):
            continue
        subagents.append(
            {
                "name": name,
                "description": str(spec.get("description", "")).strip(),
                "model": spec.get("model"),
                "tools": list(spec.get("tools", []) or []),
                "skills": list(spec.get("skills", []) or []),
                "prompt_preview": str(spec.get("system_prompt", "")).strip()[:500],
            }
        )
    return subagents


def artifact_kind(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type and mime_type.startswith("image/"):
        return "image"
    if mime_type == "application/pdf":
        return "pdf"
    if path.suffix.lower() in {".md", ".markdown"}:
        return "markdown"
    if mime_type and mime_type.startswith("text/"):
        return "text"
    if path.suffix.lower() in {".py", ".js", ".ts", ".tsx", ".vue", ".json", ".yaml", ".yml", ".css", ".html"}:
        return "code"
    return "binary"


def build_manifest(config_path: str | Path | None = None) -> dict[str, Any]:
    config = load_main_config(config_path)
    sandbox_python = str(config.backend.local_shell.python) if config.backend.local_shell else None
    return {
        "agent": {
            "name": config.name,
            "assistant_id": "content_writer",
            "model": config.model.model,
            "thread_id": config.thread_id,
            "conversation": {"max_turns": config.conversation.max_turns},
        },
        "backend": {
            "type": config.backend.type,
            "root_dir": str(config.backend.root_dir),
            "virtual_mode": config.backend.virtual_mode,
            "sandbox_python": sandbox_python,
            "requires_execute_approval": False,
        },
        "tools": list_tools(config),
        "subagents": list_subagents(config),
        "skills": list_skills(config),
        "artifacts": {
            "roots": ARTIFACT_ROOTS,
            "output_root": str(config.output_root),
            "preview_kinds": ["image", "markdown", "pdf", "text", "code", "binary"],
        },
    }
