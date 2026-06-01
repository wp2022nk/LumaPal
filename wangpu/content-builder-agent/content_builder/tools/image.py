"""General raster image generation tool backed by Qwen Image.

Domain skills compose high-quality prompts and choose output paths; this module
only enforces the writable boundary and invokes the configured image provider.
"""

from __future__ import annotations

import os
from pathlib import Path

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

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


def resolve_image_output_path(output_path: str, *, root: Path | None = None) -> Path:
    """Resolve an image target while restricting it to the output workspace."""

    raw_path = str(output_path).strip().strip("\"'")
    if not raw_path:
        raise ValueError("output_path must not be empty")

    root = (root or output_root()).resolve()
    normalized = raw_path.replace("\\", "/")
    if normalized == "/output" or normalized.startswith("/output/"):
        relative = normalized.removeprefix("/output").lstrip("/")
        target = (root / relative).resolve()
    else:
        path = Path(raw_path)
        target = path.resolve() if path.is_absolute() else (root / path).resolve()

    if target != root and root not in target.parents:
        raise ValueError("output_path must be located under /output/")
    if target.suffix.lower() != ".png":
        raise ValueError("output_path must end with .png")
    if target == root:
        raise ValueError("output_path must identify a PNG file under /output/")
    return target


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
        output_path: Target PNG path below ``/output/``, for example
            ``/output/storybooks/moon-trip/images/page-01.png``.
        size: Supported Qwen output dimensions. Square ``1024*1024`` is the
            default for illustrated pages.
    """

    if size not in ALLOWED_IMAGE_SIZES:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_SIZES))
        return f"Image generation failed; unsupported size {size!r}. Allowed sizes: {allowed}"

    try:
        resolved_output_path = resolve_image_output_path(output_path, root=output_root(runtime))
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
        return f"Image saved to {resolved_output_path}"
    except Exception as exc:
        error_path.parent.mkdir(parents=True, exist_ok=True)
        error = (
            "Image generation failed; local image was not saved. "
            f"Reason: {exc}"
        )
        error_path.write_text(error, encoding="utf-8")
        print(f"[tool:generate_image] {error}", flush=True)
        return error
