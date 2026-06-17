#!/usr/bin/env python3
"""Render a storybook manifest to HTML, audio narration, and PDF.

V2 features:
- Default ``--audio`` mode: synthesises one WAV per page using the bundled
  Qwen DashScope TTS tool (``content_builder.tools.tts._synthesize_wav``).
- ``render_audiobook_html`` produces the vertical, continuous-scroll,
  click-to-read storybook (no buttons) with a ``speechSynthesis`` fallback
  when a WAV is missing.
- ``render_print_html`` keeps 210mm square pages for the PDF.
- TTS failures are isolated per page (``*-error.txt``) and never block PDF.

Image paths in ``book.json`` must remain under ``images/``. The renderer
resolves ``/output/...`` and common relative output forms to the active
thread artifact root exposed by the deepagent workspace.
"""

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
OUTPUT_ROOT = Path(os.environ.get("CONTENT_BUILDER_OUTPUT_DIR", WORKSPACE_ROOT / "output")).resolve()
STORYBOOKS_ROOT = Path(os.environ.get("CONTENT_BUILDER_STORYBOOKS_DIR", OUTPUT_ROOT / "storybooks")).resolve()
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


def storybooks_root() -> Path:
    configured = os.environ.get("CONTENT_BUILDER_STORYBOOKS_DIR")
    return (Path(configured).resolve() if configured else (OUTPUT_ROOT / "storybooks").resolve())


def resolve_artifact_path(raw_path: str) -> Path:
    """Resolve a virtual or workspace-relative artifact path inside archive roots."""

    raw = str(raw_path).strip().strip("\"'")
    normalized = raw.replace("\\", "/")
    if normalized == "/output/storybooks" or normalized.startswith("/output/storybooks/"):
        allowed_root = storybooks_root()
        resolved = (allowed_root / normalized.removeprefix("/output/storybooks").lstrip("/")).resolve()
    elif normalized == "/output" or normalized.startswith("/output/"):
        resolved = (OUTPUT_ROOT / normalized.removeprefix("/output").lstrip("/")).resolve()
        allowed_root = OUTPUT_ROOT.resolve()
    elif normalized == "/storybooks" or normalized.startswith("/storybooks/"):
        allowed_root = storybooks_root()
        resolved = (allowed_root / normalized.removeprefix("/storybooks").lstrip("/")).resolve()
    elif normalized == "output/storybooks" or normalized.startswith("output/storybooks/"):
        allowed_root = storybooks_root()
        resolved = (allowed_root / normalized.removeprefix("output/storybooks").lstrip("/")).resolve()
    elif normalized == "output" or normalized.startswith("output/"):
        resolved = (OUTPUT_ROOT / normalized.removeprefix("output").lstrip("/")).resolve()
        allowed_root = OUTPUT_ROOT.resolve()
    elif normalized == "storybooks" or normalized.startswith("storybooks/"):
        allowed_root = storybooks_root()
        resolved = (allowed_root / normalized.removeprefix("storybooks").lstrip("/")).resolve()
    else:
        path = Path(raw)
        resolved = path.resolve() if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()
        allowed_root = OUTPUT_ROOT.resolve()

    if resolved != allowed_root and allowed_root not in resolved.parents:
        raise ValueError(f"Artifact path must be under /output/ or /storybooks/: {raw_path}")
    return resolved


def _required_string(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Missing required field: {field}")
    return text


def load_manifest(book_path: Path, output_dir: Path) -> dict[str, Any]:
    if not book_path.is_file():
        raise ValueError(f"Book manifest not found: {book_path}")

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


# ---------------------------------------------------------------------------
# TTS integration
# ---------------------------------------------------------------------------

def _synthesize_page_audio(page: dict[str, Any], audio_path: Path) -> tuple[bool, str]:
    """Synthesise a single page narration. Returns (ok, message)."""
    sys.path.insert(0, str(PROJECT_DIR))
    try:
        from content_builder.tools.tts import _synthesize_wav, MAX_TTS_CHARS  # type: ignore
    except Exception as exc:  # pragma: no cover - import failure path
        return False, f"TTS tool unavailable: {exc}"

    text = str(page.get("text", "")).strip()
    if not text:
        return False, "page text is empty; nothing to narrate"
    if len(text) > MAX_TTS_CHARS:
        return False, f"page text exceeds {MAX_TTS_CHARS} chars (got {len(text)})"

    audio_path.parent.mkdir(parents=True, exist_ok=True)
    error_path = audio_path.with_name(f"{audio_path.stem}-error.txt")
    try:
        byte_count = _synthesize_wav(text, audio_path)
        error_path.unlink(missing_ok=True)
        return True, f"wrote {byte_count} bytes"
    except Exception as exc:
        message = f"TTS generation failed: {exc}"
        try:
            error_path.write_text(message, encoding="utf-8")
        except OSError:
            pass
        return False, message


def synthesize_all_audio(book: dict[str, Any], output_dir: Path) -> dict[str, str]:
    """Generate WAV narration for every page. Returns ``{page_id: message}``."""
    audio_dir = output_dir / "audio"
    results: dict[str, str] = {}
    for page in book["pages"]:
        page_id = str(page["id"])
        audio_path = audio_dir / f"{page_id}.wav"
        ok, message = _synthesize_page_audio(page, audio_path)
        results[page_id] = ("ok" if ok else "fallback: " + message)
        print(f"[tts] {page_id}: {results[page_id]}")
    return results


# ---------------------------------------------------------------------------
# HTML templates
# ---------------------------------------------------------------------------

def render_print_html(book: dict[str, Any]) -> str:
    """Original 210mm print layout used for the PDF."""
    title = html.escape(str(book["title"]))
    pages_html: list[str] = []
    for index, page in enumerate(book["pages"], start=1):
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


def _read_aloud_text(page: dict[str, Any], title: str) -> str:
    """Choose the narration text for a page. Cover combines title + subtitle."""
    text = str(page.get("text", "")).strip()
    page_type = str(page.get("type", ""))
    if page_type == "cover" or str(page.get("id", "")).endswith("cover"):
        subtitle = text
        if not subtitle:
            return title
        return f"{title}。{subtitle}".strip()
    return text or title


def render_audiobook_html(book: dict[str, Any]) -> str:
    """Vertical storybook: continuous-scroll multi-page view, click-to-read.

    Each ``<section class="page">`` is a 210mm-style block with image and text
    visible at the same time. Clicking anywhere on a page plays that page's
    narration. There is no fade transition, no auto-advance, and no on-screen
    buttons. The HTML keeps page breaks so it can be inspected similarly to
    the generated PDF.
    """
    title = html.escape(str(book["title"]))
    pages = book["pages"]

    sections: list[str] = []
    for index, page in enumerate(pages, start=1):
        page_id = html.escape(str(page["id"]))
        image_uri = html.escape(str(page["_image_uri"]), quote=True)
        alt_text = html.escape(str(page.get("alt_text", "")))
        spoken = _read_aloud_text(page, str(book["title"]))
        spoken_json = html.escape(spoken.replace("\n", " ").strip(), quote=True)
        page_type = str(page.get("type", "page"))
        is_cover = page_type == "cover" or str(page.get("id", "")).endswith("cover")
        text_block = html.escape(str(page.get("text", "")).strip()).replace("\n", "<br>")
        layout = str(page.get("layout", "image-top-text-bottom"))
        if is_cover:
            sections.append(
                f"""  <section class="page cover" data-page="{page_id}" data-text="{spoken_json}">
    <div class="image"><img src="{image_uri}" alt="{alt_text}"></div>
    <div class="text">
      <h1>{title}</h1>
      <p>{text_block}</p>
    </div>
  </section>"""
            )
        else:
            sections.append(
                f"""  <section class="page {html.escape(layout)}" data-page="{page_id}" data-text="{spoken_json}">
    <div class="image"><img src="{image_uri}" alt="{alt_text}"></div>
    <div class="text">{text_block}</div>
    <div class="page-no">第 {index - 1} / {len(pages) - 1} 页</div>
  </section>"""
            )
    pages_markup = "\n".join(sections)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · 有声绘本</title>
<style>
:root {{
  --bg-1: #fdf6e3;
  --bg-2: #f3e3c3;
  --ink: #3b2a1e;
  --ink-soft: #6b5641;
  --accent: #d49a5a;
  --paper: rgba(255, 252, 245, 0.96);
  --paper-border: rgba(72, 50, 30, 0.10);
  --shadow: 0 18px 50px rgba(72, 50, 30, 0.14);
}}
* {{ box-sizing: border-box; }}
html, body {{
  margin: 0; padding: 0;
  background:
    radial-gradient(1200px 600px at 50% -10%, #fff8e6 0%, transparent 70%),
    linear-gradient(180deg, var(--bg-1) 0%, var(--bg-2) 100%);
  color: var(--ink);
  font-family: "Microsoft YaHei", "Noto Sans SC", "Source Han Sans SC", "PingFang SC", Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
  min-height: 100vh;
}}
body {{ display: block; }}
.topbar {{
  position: sticky; top: 0; z-index: 5;
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px;
  padding: 14px 28px;
  background: rgba(255, 252, 245, 0.78);
  backdrop-filter: blur(6px);
  border-bottom: 1px solid var(--paper-border);
}}
.topbar .title {{
  font-size: 18px; font-weight: 600; letter-spacing: 0.08em; color: #4a2f1f;
}}
.topbar .hint {{
  font-size: 12px; color: var(--ink-soft); letter-spacing: 0.04em;
}}
.topbar .hint::before {{ content: "♪  点击页面朗读"; }}
.storybook {{
  width: min(960px, 100%);
  margin: 0 auto;
  padding: 28px 24px 80px;
  display: flex; flex-direction: column; gap: 28px;
}}
.page {{
  position: relative;
  background: var(--paper);
  border-radius: 24px;
  box-shadow: var(--shadow);
  overflow: hidden;
  cursor: pointer;
  display: flex; flex-direction: column;
  page-break-after: always; break-after: page;
  transition: transform 0.25s ease, box-shadow 0.25s ease;
}}
.page:hover {{ transform: translateY(-2px); box-shadow: 0 24px 60px rgba(72, 50, 30, 0.18); }}
.page:last-child {{ page-break-after: auto; break-after: auto; }}
.page .image {{
  background: linear-gradient(180deg, #fff8e6 0%, #fceccd 100%);
  padding: 18px;
  display: flex; align-items: center; justify-content: center;
  min-height: 320px;
}}
.page .image img {{
  max-width: 100%;
  max-height: 460px;
  object-fit: contain;
  border-radius: 16px;
  box-shadow: 0 8px 22px rgba(72, 50, 30, 0.12);
  background: #fff;
}}
.page .text {{
  padding: 22px 32px 28px;
  font-size: 18px;
  line-height: 1.85;
  letter-spacing: 0.02em;
  text-align: center;
  border-top: 1px dashed var(--paper-border);
}}
.page .page-no {{
  position: absolute; right: 18px; top: 14px;
  font-size: 12px; color: var(--ink-soft);
  letter-spacing: 0.08em; opacity: 0.78;
  pointer-events: none;
}}
.page.cover .image {{ min-height: 420px; padding: 32px 24px 12px; }}
.page.cover .text {{
  padding: 28px 32px 36px;
  display: flex; flex-direction: column; align-items: center; gap: 8px;
}}
.page.cover .text h1 {{
  margin: 0; font-size: 28px; letter-spacing: 0.14em; color: #4a2f1f;
}}
.page.cover .text p {{ margin: 0; color: var(--ink-soft); font-size: 17px; }}
.page.full-bleed-caption .image {{ flex: 1 1 auto; min-height: 460px; }}
.page.full-bleed-caption .image img {{ max-height: 520px; }}
.status {{
  position: fixed; left: 18px; bottom: 16px;
  font-size: 12px; color: var(--ink-soft);
  opacity: 0.85; pointer-events: none;
  background: rgba(255, 252, 245, 0.85);
  padding: 4px 10px; border-radius: 999px;
  box-shadow: 0 4px 14px rgba(72, 50, 30, 0.10);
}}
@media (max-width: 640px) {{
  .topbar {{ padding: 10px 16px; }}
  .topbar .title {{ font-size: 16px; }}
  .storybook {{ padding: 18px 14px 60px; gap: 18px; }}
  .page {{ border-radius: 18px; }}
  .page .image {{ min-height: 220px; padding: 12px; }}
  .page .image img {{ max-height: 320px; }}
  .page .text {{ padding: 16px 18px 22px; font-size: 16px; }}
  .page.cover .text h1 {{ font-size: 22px; }}
}}
</style>
</head>
<body>
<header class="topbar">
  <div class="title">{title}</div>
  <div class="hint" aria-hidden="true"></div>
</header>
<main class="storybook" id="storybook">
{pages_markup}
</main>
<div class="status" id="status">点击任意页面开始朗读</div>
<script>
(() => {{
  const AUDIO_BASE = "./audio/";
  const PAGES = Array.from(document.querySelectorAll(".page"));
  const statusEl = document.getElementById("status");
  const synth = window.speechSynthesis;

  let lastPlayed = 0;
  let active = null; // Audio instance
  let isPlaying = false;

  function setStatus(text) {{ statusEl.textContent = text; }}

  function stopAll() {{
    if (active) {{ try {{ active.pause(); active.currentTime = 0; }} catch (e) {{}} }}
    if (synth && synth.speaking) {{ try {{ synth.cancel(); }} catch (e) {{}} }}
    isPlaying = false;
  }}

  function playPage(page) {{
    stopAll();
    if (!page) return;
    lastPlayed = Math.max(0, PAGES.indexOf(page));
    const text = (page.dataset.text || page.querySelector(".text")?.innerText || "").trim();
    const url = AUDIO_BASE + page.dataset.page + ".wav";
    const probe = new Audio();
    probe.preload = "metadata";
    probe.src = url;
    const onFallback = () => {{
      if (!text) {{ setStatus("本页没有可朗读的文本"); return; }}
      try {{ synth.cancel(); }} catch (e) {{}}
      const utt = new SpeechSynthesisUtterance(text);
      utt.lang = "zh-CN";
      utt.rate = 0.95;
      const voices = synth.getVoices();
      const zh = voices.find(v => /zh|Chinese|Mandarin/i.test(v.lang) || /zh|Chinese/i.test(v.name));
      if (zh) utt.voice = zh;
      utt.onend = () => {{ isPlaying = false; setStatus("朗读完成（浏览器语音）"); }};
      utt.onerror = () => {{ isPlaying = false; setStatus("朗读失败"); }};
      synth.speak(utt);
      isPlaying = true;
      setStatus("正在朗读…（浏览器语音）");
    }};
    probe.onloadedmetadata = () => {{
      active = new Audio(url);
      active.onended = () => {{ isPlaying = false; setStatus("朗读完成"); }};
      active.onerror = onFallback;
      active.play().then(() => {{
        isPlaying = true;
        setStatus("正在朗读…");
      }}).catch(onFallback);
    }};
    probe.onerror = onFallback;
  }}

  PAGES.forEach((page, idx) => {{
    page.addEventListener("click", () => playPage(page));
  }});

  document.addEventListener("keydown", (e) => {{
    if (e.key === " " || e.key === "Spacebar") {{
      e.preventDefault();
      playPage(PAGES[lastPlayed]);
    }} else if (e.key === "Escape") {{
      stopAll();
      setStatus("已停止");
    }} else if (e.key === "ArrowRight") {{
      e.preventDefault();
      const next = Math.min(PAGES.length - 1, lastPlayed + 1);
      PAGES[next]?.scrollIntoView({{ behavior: "smooth", block: "start" }});
      lastPlayed = next;
    }} else if (e.key === "ArrowLeft") {{
      e.preventDefault();
      const prev = Math.max(0, lastPlayed - 1);
      PAGES[prev]?.scrollIntoView({{ behavior: "smooth", block: "start" }});
      lastPlayed = prev;
    }}
  }});

  if (synth && typeof synth.onvoiceschanged !== "undefined") {{
    synth.onvoiceschanged = () => {{}};
  }}

  window.addEventListener("beforeunload", () => {{ try {{ synth && synth.cancel(); }} catch (e) {{}} }});
}})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# PDF rendering
# ---------------------------------------------------------------------------

def display_pdf_filename(manifest: dict[str, Any]) -> str:
    title = str(manifest.get("title") or manifest.get("_slug") or "storybook").strip()
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title)
    filename = re.sub(r"\s+", "", filename).strip(". ")
    return f"{filename or manifest['_slug']}.pdf"


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


def print_pdf(book: dict[str, Any], pdf_path: Path) -> None:
    """Render the PDF from a dedicated 210mm-square print HTML (separate from
    the scroll-mode ``book.html``). Keeps image dimensions bounded and the
    page size consistent across runs."""
    chrome = find_chrome()
    print_html = render_print_html(book)
    with tempfile.TemporaryDirectory(prefix="storybook-chrome-", dir=str(pdf_path.parent)) as profile:
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".html",
            delete=False,
            encoding="utf-8",
            dir=str(pdf_path.parent),
        ) as tmp_html:
            tmp_html.write(print_html)
            tmp_html_path = Path(tmp_html.name)
        try:
            command = [
                str(chrome),
                "--headless=new",
                "--disable-gpu",
                "--allow-file-access-from-files",
                "--no-pdf-header-footer",
                f"--user-data-dir={profile}",
                f"--print-to-pdf={pdf_path}",
                tmp_html_path.as_uri(),
            ]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=90)
        finally:
            try:
                tmp_html_path.unlink()
            except OSError:
                pass
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"Chrome PDF rendering failed ({completed.returncode}): {details}")
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError(f"Chrome did not produce a non-empty PDF: {pdf_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Render storybook JSON to HTML, audio, and PDF.")
    parser.add_argument("--book", required=True, help="book.json path under /output/ or /storybooks/")
    parser.add_argument("--output-dir", required=True, help="Storybook directory under /storybooks/ or /output/")
    parser.add_argument(
        "--audio",
        dest="audio",
        action="store_true",
        default=True,
        help="Synthesise per-page WAV narration using bundled TTS (default: on)",
    )
    parser.add_argument(
        "--no-audio",
        dest="audio",
        action="store_false",
        help="Skip WAV synthesis; the HTML will rely on speechSynthesis fallback only",
    )
    parser.add_argument(
        "--no-pdf",
        action="store_true",
        help="Skip PDF generation even when Chrome is available",
    )
    args = parser.parse_args()

    try:
        book_path = resolve_artifact_path(args.book)
        output_dir = resolve_artifact_path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = load_manifest(book_path, output_dir)

        audio_results: dict[str, str] = {}
        if args.audio:
            print(f"[render] synthesising audio for {len(manifest['pages'])} page(s)…")
            audio_results = synthesize_all_audio(manifest, output_dir)
            ok_count = sum(1 for v in audio_results.values() if v == "ok")
            print(f"[render] audio: {ok_count}/{len(audio_results)} pages synthesised")

        html_path = output_dir / "book.html"
        html_path.write_text(render_audiobook_html(manifest), encoding="utf-8")
        print(f"HTML saved to: {html_path}")

        if not args.no_pdf:
            pdf_path = output_dir / display_pdf_filename(manifest)
            print_pdf(manifest, pdf_path)
            print(f"PDF saved to: {pdf_path}")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Storybook render failed: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
