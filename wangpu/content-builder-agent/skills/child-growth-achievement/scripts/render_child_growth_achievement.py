#!/usr/bin/env python3
"""Render a child-facing growth achievement panel to HTML and optional PDF."""

from __future__ import annotations

import argparse
import base64
import html
import json
import mimetypes
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = PROJECT_DIR.parents[1]
OUTPUT_ROOT = Path(os.environ.get("CONTENT_BUILDER_OUTPUT_DIR", WORKSPACE_ROOT / "output")).resolve()
REPORTS_ROOT = Path(os.environ.get("CONTENT_BUILDER_REPORTS_DIR", OUTPUT_ROOT / "reports")).resolve()
CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
]


def esc(value: Any) -> str:
    return html.escape(str(value or ""))


def resolve_output_dir(raw_path: str) -> Path:
    raw = str(raw_path).strip().strip("\"'")
    normalized = raw.replace("\\", "/")
    if normalized == "/reports" or normalized.startswith("/reports/"):
        resolved = (REPORTS_ROOT / normalized.removeprefix("/reports").lstrip("/")).resolve()
        allowed_root = REPORTS_ROOT.resolve()
    elif normalized == "/output" or normalized.startswith("/output/"):
        resolved = (OUTPUT_ROOT / normalized.removeprefix("/output").lstrip("/")).resolve()
        allowed_root = OUTPUT_ROOT.resolve()
    elif normalized == "reports" or normalized.startswith("reports/"):
        resolved = (REPORTS_ROOT / normalized.removeprefix("reports").lstrip("/")).resolve()
        allowed_root = REPORTS_ROOT.resolve()
    elif normalized == "output" or normalized.startswith("output/"):
        resolved = (OUTPUT_ROOT / normalized.removeprefix("output").lstrip("/")).resolve()
        allowed_root = OUTPUT_ROOT.resolve()
    else:
        path = Path(raw)
        resolved = path.resolve() if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()
        allowed_root = WORKSPACE_ROOT.resolve()
    if not (resolved == allowed_root or allowed_root in resolved.parents):
        raise ValueError(f"Output path must stay inside the workspace/output roots: {raw_path}")
    return resolved


def resolve_image_path(raw_path: str, *, base_dir: Path | None = None) -> Path | None:
    if not raw_path:
        return None
    raw = str(raw_path).strip().strip("\"'")
    path = Path(raw)
    candidates = []
    if path.is_absolute():
        candidates.append(path)
    if base_dir is not None:
        candidates.append(base_dir / raw)
    candidates.extend([WORKSPACE_ROOT / raw, PROJECT_DIR / raw])
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    return None


def image_to_data_uri(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def image_src(raw_path: str, *, base_dir: Path | None = None, prefer_data: bool = False) -> str:
    path = resolve_image_path(raw_path, base_dir=base_dir)
    if not path:
        return ""
    if prefer_data:
        try:
            return image_to_data_uri(path)
        except OSError:
            return ""
    return path.as_uri()


def render_stars(level: int) -> str:
    safe_level = max(1, min(5, int(level or 1)))
    return "".join('<span class="star filled"></span>' if i < safe_level else '<span class="star"></span>' for i in range(5))


def render_html(data: dict[str, Any], *, base_dir: Path, prefer_data: bool = False) -> str:
    achievements = data.get("achievements") or []
    badge_map = data.get("badge_map") or []
    works = data.get("works") or []
    generated_images = data.get("generated_images") or {}
    challenge_next = data.get("challenge_next") or {}

    hero_src = image_src(generated_images.get("hero_adventure", ""), base_dir=base_dir, prefer_data=prefer_data)
    badge_src = image_src(generated_images.get("badge_map", ""), base_dir=base_dir, prefer_data=prefer_data)
    lab_src = image_src(generated_images.get("curiosity_lab", ""), base_dir=base_dir, prefer_data=prefer_data)

    achievement_cards = []
    for index, item in enumerate(achievements):
        tone = ["sun", "mint", "sky", "berry", "peach"][index % 5]
        achievement_cards.append(
            f"""<article class="achievement-card {tone}">
  <div class="achievement-medal">{esc(item.get("badge"))}</div>
  <div class="achievement-copy">
    <p class="card-label">{esc(item.get("label"))}</p>
    <h3>{esc(item.get("title"))}</h3>
    <p>{esc(item.get("message"))}</p>
    <div class="stars" aria-label="{esc(item.get("title"))} 星级">{render_stars(item.get("level", 4))}</div>
    <small>{esc(item.get("evidence"))}</small>
  </div>
</article>"""
        )

    badge_nodes = []
    for index, item in enumerate(badge_map):
        badge_nodes.append(
            f"""<li style="--step:{index + 1}">
  <span>{esc(item.get("badge"))}</span>
  <strong>{esc(item.get("title"))}</strong>
  <small>{esc(item.get("caption"))}</small>
</li>"""
        )

    work_cards = []
    for item in works:
        cover = image_src(item.get("cover_image", ""), base_dir=base_dir, prefer_data=prefer_data)
        cover_html = f'<img src="{cover}" alt="{esc(item.get("title"))} 封面" loading="lazy">' if cover else '<div class="work-placeholder"></div>'
        link = esc(item.get("link"))
        tag = "a" if link else "article"
        href = f' href="{link}"' if link else ""
        work_cards.append(
            f"""<{tag} class="work-card"{href}>
  <div class="work-cover">{cover_html}</div>
  <span>{esc(item.get("type"))}</span>
  <strong>{esc(item.get("title"))}</strong>
  <small>{esc(item.get("highlight"))}</small>
</{tag}>"""
        )

    challenge_items = "".join(f"<li>{esc(item)}</li>" for item in challenge_next.get("steps", []))
    hero_image = f'<img src="{hero_src}" alt="成长成就主视觉">' if hero_src else ""
    badge_image = f'<img src="{badge_src}" alt="成长徽章地图">' if badge_src else ""
    lab_image = f'<img src="{lab_src}" alt="好奇心实验室">'
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(data.get("title"))}</title>
<style>
@page {{ size: A4; margin: 9mm; }}
:root {{
  --ink: #26313a;
  --muted: #6b6f7a;
  --paper: #fff9ec;
  --sun: #ffd15c;
  --peach: #ff8f70;
  --mint: #64d6a4;
  --sky: #65b8ff;
  --berry: #b978ff;
  --line: rgba(38, 49, 58, .12);
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  color: var(--ink);
  background:
    radial-gradient(circle at 16% 10%, rgba(255, 209, 92, .34), transparent 28%),
    radial-gradient(circle at 88% 18%, rgba(101, 184, 255, .28), transparent 28%),
    linear-gradient(180deg, #fff7df 0%, #eefaff 58%, #fff2e8 100%);
  font-family: "Microsoft YaHei", "Noto Sans SC", "Source Han Sans SC", Arial, sans-serif;
}}
a {{ color: inherit; text-decoration: none; }}
.page {{ max-width: 1180px; margin: 0 auto; padding: 24px; }}
.hero {{
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(300px, .95fr);
  min-height: 520px;
  gap: 28px;
  align-items: center;
  overflow: hidden;
  border: 3px solid rgba(255, 255, 255, .86);
  border-radius: 30px;
  background:
    linear-gradient(90deg, rgba(255,255,255,.2) 1px, transparent 1px) 0 0 / 34px 34px,
    linear-gradient(135deg, #2b9bd9 0%, #6bd8b2 48%, #ffe07a 100%);
  box-shadow: 0 28px 70px rgba(53, 88, 114, .22);
}}
.hero::before {{
  content: "";
  position: absolute;
  inset: 18px;
  border: 2px dashed rgba(255,255,255,.55);
  border-radius: 24px;
  pointer-events: none;
}}
.hero-copy {{ position: relative; z-index: 1; padding: 46px 0 46px 44px; color: #fffdf5; }}
.period {{ display: inline-flex; padding: 9px 14px; border-radius: 999px; background: rgba(255,255,255,.24); font-weight: 900; }}
h1 {{ max-width: 680px; margin: 18px 0 14px; font-size: clamp(42px, 6vw, 86px); line-height: 1.02; letter-spacing: 0; }}
.subtitle {{ max-width: 620px; margin: 0; font-size: 22px; line-height: 1.6; font-weight: 800; }}
.quote {{
  max-width: 620px;
  margin-top: 24px;
  padding: 18px 22px;
  border-radius: 20px;
  color: #4d3412;
  background: rgba(255,255,255,.88);
  box-shadow: 0 12px 28px rgba(78, 87, 90, .13);
  font-size: 22px;
  line-height: 1.55;
  font-weight: 900;
}}
.hero-art {{ position: relative; min-height: 470px; align-self: stretch; }}
.hero-art img {{
  position: absolute;
  inset: 28px 28px 28px 0;
  width: calc(100% - 28px);
  height: calc(100% - 56px);
  object-fit: cover;
  border: 4px solid rgba(255,255,255,.9);
  border-radius: 28px;
  box-shadow: 0 24px 48px rgba(47, 72, 86, .2);
}}
.section {{ margin-top: 26px; }}
.section-title {{ display: flex; align-items: center; gap: 10px; margin: 0 0 16px; font-size: 30px; }}
.section-title::before {{ content: ""; width: 18px; height: 18px; border-radius: 50%; background: var(--peach); box-shadow: 24px 0 var(--sun), 48px 0 var(--mint); }}
.achievement-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
.achievement-card {{
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
  gap: 16px;
  min-height: 190px;
  padding: 18px;
  border: 2px solid rgba(255,255,255,.78);
  border-radius: 24px;
  background: rgba(255,255,255,.78);
  box-shadow: 0 16px 34px rgba(74, 91, 104, .13);
}}
.achievement-card.sun {{ background: linear-gradient(135deg, rgba(255, 239, 184, .95), rgba(255,255,255,.82)); }}
.achievement-card.mint {{ background: linear-gradient(135deg, rgba(203, 255, 228, .95), rgba(255,255,255,.82)); }}
.achievement-card.sky {{ background: linear-gradient(135deg, rgba(208, 237, 255, .95), rgba(255,255,255,.82)); }}
.achievement-card.berry {{ background: linear-gradient(135deg, rgba(235, 216, 255, .95), rgba(255,255,255,.82)); }}
.achievement-card.peach {{ background: linear-gradient(135deg, rgba(255, 219, 207, .95), rgba(255,255,255,.82)); }}
.achievement-medal {{
  display: grid;
  width: 78px;
  height: 78px;
  place-items: center;
  border-radius: 24px;
  background: #fffdf7;
  box-shadow: inset 0 -5px 0 rgba(38,49,58,.08), 0 12px 20px rgba(66,77,83,.12);
  font-size: 34px;
  font-weight: 900;
}}
.card-label {{ margin: 0 0 4px; color: #7a5d14; font-size: 13px; font-weight: 900; }}
.achievement-card h3 {{ margin: 0; font-size: 24px; line-height: 1.2; }}
.achievement-card p:not(.card-label) {{ margin: 9px 0 8px; color: #39454d; font-size: 16px; line-height: 1.6; }}
.achievement-card small {{ color: var(--muted); line-height: 1.5; }}
.stars {{ display: flex; gap: 5px; margin: 8px 0; }}
.star {{ width: 16px; height: 16px; clip-path: polygon(50% 0, 62% 34%, 98% 35%, 69% 56%, 79% 91%, 50% 70%, 21% 91%, 31% 56%, 2% 35%, 38% 34%); background: rgba(38,49,58,.15); }}
.star.filled {{ background: #ffbb2e; }}
.map-lab {{ display: grid; grid-template-columns: minmax(0, .95fr) minmax(0, 1.05fr); gap: 16px; align-items: stretch; }}
.badge-map, .lab-panel {{ border: 2px solid rgba(255,255,255,.8); border-radius: 26px; background: rgba(255,255,255,.76); box-shadow: 0 16px 34px rgba(74, 91, 104, .12); overflow: hidden; }}
.badge-map {{ padding: 18px; }}
.badge-map img, .lab-panel img {{ display: block; width: 100%; aspect-ratio: 16 / 10; object-fit: cover; border-radius: 18px; }}
.badge-map ol {{ list-style: none; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; padding: 0; margin: 16px 0 0; }}
.badge-map li {{ display: grid; gap: 4px; padding: 12px; border-radius: 16px; background: #fff8df; }}
.badge-map li span {{ font-size: 24px; }}
.badge-map li strong {{ font-size: 16px; }}
.badge-map li small {{ color: var(--muted); line-height: 1.45; }}
.lab-panel {{ display: grid; align-content: start; gap: 14px; padding: 18px; }}
.lab-panel h3 {{ margin: 0; font-size: 25px; }}
.lab-panel p {{ margin: 0; color: #3d4d55; font-size: 17px; line-height: 1.7; }}
.work-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }}
.work-card {{ display: grid; gap: 8px; padding: 12px; border: 2px solid rgba(255,255,255,.78); border-radius: 20px; background: rgba(255,255,255,.78); box-shadow: 0 12px 24px rgba(74, 91, 104, .1); }}
.work-cover {{ overflow: hidden; border-radius: 15px; background: #e7f3ff; }}
.work-cover img, .work-placeholder {{ display: block; width: 100%; aspect-ratio: 1 / 1; object-fit: cover; }}
.work-placeholder {{ background: linear-gradient(135deg, #ffe07a, #65b8ff); }}
.work-card span {{ color: #7a5d14; font-size: 12px; font-weight: 900; }}
.work-card strong {{ font-size: 17px; line-height: 1.35; }}
.work-card small {{ color: var(--muted); line-height: 1.45; }}
.challenge {{ display: grid; grid-template-columns: minmax(0, .8fr) minmax(0, 1.2fr); gap: 18px; padding: 22px; border-radius: 28px; background: linear-gradient(135deg, #2f8dd6, #49caa2); color: #fffdf5; box-shadow: 0 18px 42px rgba(44, 98, 122, .2); }}
.challenge h2 {{ margin: 0 0 10px; font-size: 32px; }}
.challenge p {{ margin: 0; font-size: 18px; line-height: 1.7; }}
.challenge ul {{ display: grid; gap: 10px; margin: 0; padding: 0; list-style: none; }}
.challenge li {{ padding: 12px 14px; border-radius: 16px; background: rgba(255,255,255,.2); font-size: 17px; font-weight: 800; }}
@media (max-width: 860px) {{
  .page {{ padding: 14px; }}
  .hero, .map-lab, .challenge {{ grid-template-columns: 1fr; }}
  .hero-copy {{ padding: 30px 22px 0; }}
  .hero-art {{ min-height: 310px; }}
  .hero-art img {{ inset: 14px 22px 24px; width: calc(100% - 44px); height: calc(100% - 38px); }}
  .achievement-grid, .work-grid {{ grid-template-columns: 1fr; }}
  .badge-map ol {{ grid-template-columns: 1fr; }}
}}
@media print {{
  body {{ background: #fff8e8; }}
  .page {{ padding: 0; }}
  .hero, .achievement-card, .badge-map, .lab-panel, .work-card, .challenge {{ box-shadow: none; break-inside: avoid; }}
  .section {{ break-inside: avoid; }}
}}
</style>
</head>
<body>
<main class="page">
  <section class="hero">
    <div class="hero-copy">
      <span class="period">{esc(data.get("period"))}</span>
      <h1>{esc(data.get("title"))}</h1>
      <p class="subtitle">{esc(data.get("subtitle"))}</p>
      <div class="quote">“{esc(data.get("hero_quote"))}”<br><small>{esc(data.get("quote_source"))}</small></div>
    </div>
    <div class="hero-art">{hero_image}</div>
  </section>

  <section class="section">
    <h2 class="section-title">本周亮闪闪称号</h2>
    <div class="achievement-grid">{"".join(achievement_cards)}</div>
  </section>

  <section class="section map-lab">
    <div class="badge-map">
      {badge_image}
      <ol>{"".join(badge_nodes)}</ol>
    </div>
    <div class="lab-panel">
      {lab_image}
      <h3>{esc(data.get("lab_title"))}</h3>
      <p>{esc(data.get("lab_message"))}</p>
    </div>
  </section>

  <section class="section">
    <h2 class="section-title">我的作品宝盒</h2>
    <div class="work-grid">{"".join(work_cards)}</div>
  </section>

  <section class="section challenge">
    <div>
      <h2>{esc(challenge_next.get("title"))}</h2>
      <p>{esc(challenge_next.get("message"))}</p>
    </div>
    <ul>{challenge_items}</ul>
  </section>
</main>
</body>
</html>"""
    return html_doc


def find_chrome() -> Path | None:
    for candidate in CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def render_pdf(html_path: Path, pdf_path: Path) -> bool:
    chrome = find_chrome()
    if not chrome:
        return False
    with tempfile.TemporaryDirectory(prefix="child-achievement-chrome-") as profile_dir:
        command = [
            str(chrome),
            "--headless=new",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-pdf-header-footer",
            f"--user-data-dir={profile_dir}",
            f"--print-to-pdf={pdf_path}",
            html_path.as_uri(),
        ]
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    return completed.returncode == 0 and pdf_path.is_file()


def safe_filename(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "-", value).strip()
    return cleaned or "儿童成长成就"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, help="Path to child-achievement-data.json")
    parser.add_argument("--output-dir", required=True, help="Directory for index.html and optional PDF")
    parser.add_argument("--pdf", action="store_true", help="Also render a PDF using local Chrome/Edge")
    parser.add_argument("--embed-images", action="store_true", help="Embed images as data URIs in the HTML")
    args = parser.parse_args()

    data_path = Path(args.data).resolve()
    output_dir = resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(data_path.read_text(encoding="utf-8"))
    html_text = render_html(data, base_dir=data_path.parent, prefer_data=args.embed_images)
    html_path = output_dir / "index.html"
    html_path.write_text(html_text, encoding="utf-8")

    print(f"Wrote {html_path}")
    if args.pdf:
        pdf_name = safe_filename(str(data.get("pdf_title") or data.get("title") or "儿童成长成就")) + ".pdf"
        pdf_path = output_dir / pdf_name
        if render_pdf(html_path, pdf_path):
            print(f"Wrote {pdf_path}")
        else:
            print("PDF rendering skipped: Chrome/Edge headless unavailable or failed", file=os.sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
