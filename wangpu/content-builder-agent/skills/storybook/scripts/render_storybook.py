#!/usr/bin/env python3
"""Render a storybook manifest to HTML and PDF using local Chrome Headless."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = PROJECT_DIR.parents[1]
OUTPUT_ROOT = WORKSPACE_ROOT / "output"
ALLOWED_LAYOUTS = {
    "full-bleed-title",
    "image-top-text-bottom",
    "full-bleed-caption",
}
CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
]


def resolve_artifact_path(raw_path: str) -> Path:
    """Resolve a virtual or workspace-relative artifact path inside /output."""

    raw = str(raw_path).strip().strip("\"'")
    normalized = raw.replace("\\", "/")
    if normalized == "/output" or normalized.startswith("/output/"):
        resolved = (OUTPUT_ROOT / normalized.removeprefix("/output").lstrip("/")).resolve()
    else:
        path = Path(raw)
        resolved = path.resolve() if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()

    output_root = OUTPUT_ROOT.resolve()
    if resolved != output_root and output_root not in resolved.parents:
        raise ValueError(f"Artifact path must be under /output/: {raw_path}")
    return resolved


def _required_string(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Missing required field: {field}")
    return text


def load_manifest(book_path: Path, output_dir: Path) -> dict[str, Any]:
    if not book_path.is_file():
        raise ValueError(f"Book manifest not found: {book_path}")

    # ``utf-8-sig`` accepts both regular UTF-8 and Windows-authored JSON with a BOM.
    with book_path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Book manifest must be a JSON object")

    slug = _required_string(data.get("slug"), "slug")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("slug must be lowercase ASCII words separated by hyphens")
    _required_string(data.get("title"), "title")

    page_format = data.get("format") or {}
    if page_format.get("page_size", "square-210mm") != "square-210mm":
        raise ValueError("Only format.page_size='square-210mm' is supported")

    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("pages must be a non-empty list")
    declared_count = page_format.get("page_count")
    if declared_count is not None and int(declared_count) != len(pages):
        raise ValueError("format.page_count must match the number of pages")

    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise ValueError(f"pages[{index}] must be an object")
        _required_string(page.get("id"), f"pages[{index}].id")
        layout = _required_string(page.get("layout"), f"pages[{index}].layout")
        if layout not in ALLOWED_LAYOUTS:
            raise ValueError(f"Unsupported layout on page {index + 1}: {layout}")
        image_rel = _required_string(page.get("image_path"), f"pages[{index}].image_path")
        image_path = (output_dir / image_rel).resolve()
        if output_dir.resolve() not in image_path.parents:
            raise ValueError(f"Image path escapes storybook directory: {image_rel}")
        if image_path.suffix.lower() != ".png" or not image_path.is_file():
            raise ValueError(f"Required page image not found: {image_path}")
        page["_image_uri"] = image_path.as_uri()

    data["_slug"] = slug
    return data


def render_html(book: dict[str, Any]) -> str:
    title = html.escape(str(book["title"]))
    pages_html: list[str] = []
    pages = book["pages"]
    for index, page in enumerate(pages, start=1):
        layout = html.escape(str(page["layout"]))
        text = html.escape(str(page.get("text", "")).strip()).replace("\n", "<br>")
        alt_text = html.escape(str(page.get("alt_text", "")))
        image_uri = html.escape(str(page["_image_uri"]), quote=True)
        page_type = str(page.get("type", "page"))
        if layout == "full-bleed-title":
            heading = title if page_type == "cover" or index == 1 else ""
            pages_html.append(
                f"""<section class="page {layout}">
  <img src="{image_uri}" alt="{alt_text}">
  <div class="title-card">{f'<h1>{heading}</h1>' if heading else ''}<p>{text}</p></div>
</section>"""
            )
        elif layout == "full-bleed-caption":
            pages_html.append(
                f"""<section class="page {layout}">
  <img src="{image_uri}" alt="{alt_text}">
  <div class="caption">{text}</div><span class="page-no">{index}</span>
</section>"""
            )
        else:
            pages_html.append(
                f"""<section class="page {layout}">
  <div class="image-frame"><img src="{image_uri}" alt="{alt_text}"></div>
  <div class="reading-text">{text}</div><span class="page-no">{index}</span>
</section>"""
            )

    pages_markup = "\n".join(pages_html)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
@page {{ size: 210mm 210mm; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; padding: 0; background: #f5efe4; }}
body {{ font-family: "Microsoft YaHei", "Noto Sans SC", "Source Han Sans SC", Arial, sans-serif; color: #33261f; }}
.page {{ width: 210mm; height: 210mm; position: relative; overflow: hidden; background: #fffaf0; page-break-after: always; break-after: page; }}
.page:last-child {{ page-break-after: auto; break-after: auto; }}
.page img {{ display: block; }}
.full-bleed-title > img, .full-bleed-caption > img {{ width: 100%; height: 100%; object-fit: cover; }}
.title-card {{ position: absolute; left: 13mm; right: 13mm; bottom: 14mm; padding: 8mm 10mm; text-align: center; border-radius: 7mm; background: rgba(255,250,241,.91); box-shadow: 0 2mm 7mm rgba(42,26,14,.13); }}
.title-card h1 {{ margin: 0 0 3mm; font-size: 25pt; letter-spacing: .1em; color: #493126; }}
.title-card p {{ margin: 0; font-size: 13.5pt; line-height: 1.75; }}
.image-top-text-bottom {{ padding: 11mm 11mm 13mm; display: flex; flex-direction: column; gap: 8mm; }}
.image-frame {{ flex: 1; border-radius: 8mm; overflow: hidden; background: #eee3cf; box-shadow: 0 2mm 7mm rgba(42,26,14,.10); }}
.image-frame img {{ width: 100%; height: 100%; object-fit: cover; }}
.reading-text {{ min-height: 38mm; padding: 6mm 8mm; border-radius: 6mm; background: #fff; font-size: 16pt; line-height: 1.7; letter-spacing: .02em; }}
.caption {{ position: absolute; left: 12mm; right: 12mm; bottom: 13mm; padding: 6mm 8mm; border-radius: 6mm; background: rgba(255,252,245,.93); font-size: 16pt; line-height: 1.7; }}
.page-no {{ position: absolute; right: 7mm; bottom: 5mm; font-size: 9pt; color: rgba(60,44,35,.52); }}
@media print {{ html, body {{ background: transparent; }} }}
</style>
</head>
<body>
{pages_markup}
</body>
</html>
"""


def find_chrome() -> Path:
    configured = os.environ.get("CHROME_PATH")
    if configured:
        candidate = Path(configured)
        if candidate.is_file():
            return candidate
        raise ValueError(f"CHROME_PATH does not reference an executable: {candidate}")
    for candidate in CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise ValueError("Chrome or Edge executable not found; set CHROME_PATH to render PDF")


def print_pdf(html_path: Path, pdf_path: Path) -> None:
    chrome = find_chrome()
    with tempfile.TemporaryDirectory(prefix="storybook-chrome-", dir=str(pdf_path.parent)) as profile:
        command = [
            str(chrome),
            "--headless=new",
            "--disable-gpu",
            "--allow-file-access-from-files",
            "--no-pdf-header-footer",
            f"--user-data-dir={profile}",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=90)
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"Chrome PDF rendering failed ({completed.returncode}): {details}")
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError(f"Chrome did not produce a non-empty PDF: {pdf_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Render storybook JSON to HTML and PDF.")
    parser.add_argument("--book", required=True, help="book.json path under /output/")
    parser.add_argument("--output-dir", required=True, help="Storybook directory under /output/")
    args = parser.parse_args()

    try:
        book_path = resolve_artifact_path(args.book)
        output_dir = resolve_artifact_path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = load_manifest(book_path, output_dir)
        html_path = output_dir / "book.html"
        pdf_path = output_dir / f"{manifest['_slug']}.pdf"
        html_path.write_text(render_html(manifest), encoding="utf-8")
        print_pdf(html_path, pdf_path)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Storybook render failed: {exc}", file=sys.stderr)
        return 1

    print(f"HTML saved to: {html_path}")
    print(f"PDF saved to: {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
