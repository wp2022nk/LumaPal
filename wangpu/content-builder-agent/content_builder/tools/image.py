"""General raster image generation tool backed by Qwen Image.

Domain skills compose high-quality prompts and choose output paths; this module
only enforces the writable boundary and invokes the configured image provider.
"""

from __future__ import annotations

import os
from pathlib import Path

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from content_builder import archive
from content_builder.config import load_main_config
from content_builder.thread_storage import runtime_thread_id, thread_paths
from qwen_image_tool import DEFAULT_QWEN_IMAGE_MODEL, generate_qwen_image


QWEN_IMAGE_MODEL = os.environ.get("QWEN_IMAGE_MODEL", DEFAULT_QWEN_IMAGE_MODEL)
ALLOWED_IMAGE_SIZES = {
    "1024*1024",
    "1328*1328",
    "1664*928",
    "928*1664",
    "1472*1140",
    "1140*1472",
}


def output_root(runtime: ToolRuntime | None = None) -> Path:
    config = load_main_config()
    root = (
        thread_paths(runtime_thread_id(runtime), output_root=config.output_root).artifacts
        if runtime is not None
        else config.output_root.resolve()
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def artifact_roots(runtime: ToolRuntime | None = None) -> dict[str, Path]:
    config = load_main_config()
    if runtime is not None:
        paths = thread_paths(runtime_thread_id(runtime), output_root=config.output_root)
        roots = {
            "/output/storybooks": paths.storybooks,
            "/output/games": paths.games,
            "/output/reports": paths.reports,
            "/output/growth-report": paths.reports / "growth-report",
            "/output": paths.artifacts,
            "/storybooks": paths.storybooks,
            "/games": paths.games,
            "/reports": paths.reports,
            "/uploads": paths.uploads,
            "/workspace": paths.workspace,
        }
    else:
        root = output_root().resolve()
        roots = {
            "/output/storybooks": root / "storybooks",
            "/output/games": root / "games",
            "/output/reports": root / "reports",
            "/output/growth-report": root / "reports" / "growth-report",
            "/output": root,
            "/storybooks": root / "storybooks",
            "/games": root / "games",
            "/reports": root / "reports",
            "/uploads": root / "uploads",
            "/workspace": root / "workspace",
        }
    for directory in roots.values():
        directory.mkdir(parents=True, exist_ok=True)
    return roots


def resolve_artifact_output_path(
    output_path: str,
    *,
    runtime: ToolRuntime | None = None,
    root: Path | None = None,
    allowed_suffixes: set[str],
    error_prefix: str = "output_path",
) -> Path:
    """Resolve a tool output target under one of the archive virtual roots."""

    raw_path = str(output_path).strip().strip("\"'")
    if not raw_path:
        raise ValueError(f"{error_prefix} must not be empty")

    roots = {"/output": (root or output_root()).resolve()} if root is not None else artifact_roots(runtime)
    normalized = raw_path.replace("\\", "/")
    selected_virtual = "/output"
    selected_root = roots["/output"].resolve()
    relative = normalized
    for virtual_root, physical_root in sorted(roots.items(), key=lambda item: len(item[0]), reverse=True):
        if normalized == virtual_root or normalized.startswith(f"{virtual_root}/"):
            selected_virtual = virtual_root
            selected_root = physical_root.resolve()
            relative = normalized.removeprefix(virtual_root).lstrip("/")
            break
    path = Path(relative if selected_virtual != "/output" or normalized.startswith("/") else raw_path)
    target = (selected_root / relative).resolve() if normalized.startswith("/") else (
        path.resolve() if path.is_absolute() else (selected_root / path).resolve()
    )

    if target != selected_root and selected_root not in target.parents:
        raise ValueError(f"{error_prefix} must be located under /output/ or another archive output directory")
    if target.suffix.lower() not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise ValueError(f"{error_prefix} must end with one of: {allowed}")
    if target == selected_root:
        raise ValueError(f"{error_prefix} must identify a file")
    return target


def resolve_image_output_path(
    output_path: str,
    *,
    runtime: ToolRuntime | None = None,
    root: Path | None = None,
) -> Path:
    """Resolve an image target while restricting it to archive output roots."""

    return resolve_artifact_output_path(
        output_path,
        runtime=runtime,
        root=root,
        allowed_suffixes={".png"},
    )


def _error_path_for(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}-error.txt")


@tool
def generate_image(
    prompt: str,
    output_path: str,
    runtime: ToolRuntime,
    size: str = "1024*1024",
) -> str:
    """Generate a PNG image for a user artifact with Qwen Image.

    Parameters:
        prompt: Detailed visual-generation prompt assembled by the active skill.
        output_path: Target PNG path below an archive virtual root. For
            storybooks prefer ``/storybooks/moon-trip/images/page-01.png``;
            loose images may use ``/output/...``.
        size: Supported Qwen output dimensions. Square ``1024*1024`` is the
            default for illustrated pages.
    """

    if size not in ALLOWED_IMAGE_SIZES:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_SIZES))
        return f"Image generation failed; unsupported size {size!r}. Allowed sizes: {allowed}"

    try:
        try:
            thread_id = runtime_thread_id(runtime)
            resolved_output_path = resolve_image_output_path(output_path, runtime=runtime)
        except ValueError:
            thread_id = ""
            resolved_output_path = resolve_image_output_path(output_path)
    except ValueError as exc:
        return f"Image generation failed; local image was not saved. Reason: {exc}"

    error_path = _error_path_for(resolved_output_path)
    try:
        print(
            f"\n[tool:generate_image] Using {QWEN_IMAGE_MODEL} to generate image: "
            f"{resolved_output_path}",
            flush=True,
        )
        result = generate_qwen_image(
            prompt,
            resolved_output_path,
            model=QWEN_IMAGE_MODEL,
            size=size,
        )
        error_path.unlink(missing_ok=True)
        print(
            f"[tool:generate_image] Image saved: {resolved_output_path} "
            f"({result.get('bytes', 0)} bytes)",
            flush=True,
        )
        if thread_id:
            archive.update_manifest(thread_id)
        return f"Image saved to {resolved_output_path}"
    except Exception as exc:
        error_path.parent.mkdir(parents=True, exist_ok=True)
        error = (
            "Image generation failed; local image was not saved. "
            f"Reason: {exc}"
        )
        error_path.write_text(error, encoding="utf-8")
        if thread_id:
            archive.update_manifest(thread_id)
        print(f"[tool:generate_image] {error}", flush=True)
        return error
