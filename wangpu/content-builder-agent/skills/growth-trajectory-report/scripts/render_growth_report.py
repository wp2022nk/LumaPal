#!/usr/bin/env python3
"""Render a growth trajectory report JSON to HTML and optional PDF."""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = PROJECT_DIR.parents[1]
OUTPUT_ROOT = Path(os.environ.get("CONTENT_BUILDER_OUTPUT_DIR", WORKSPACE_ROOT / "output")).resolve()
CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
]


def resolve_artifact_path(raw_path: str) -> Path:
    raw = str(raw_path).strip().strip("\"'")
    normalized = raw.replace("\\", "/")
    if normalized == "/output" or normalized.startswith("/output/"):
        resolved = (OUTPUT_ROOT / normalized.removeprefix("/output").lstrip("/")).resolve()
    else:
        path = Path(raw)
        resolved = path.resolve() if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()
    if OUTPUT_ROOT != resolved and OUTPUT_ROOT not in resolved.parents:
        raise ValueError(f"Report artifact path must be under /output/: {raw_path}")
    return resolved


def text(value: Any) -> str:
    return html.escape(str(value or ""))


def list_items(items: list[Any], *, class_name: str = "") -> str:
    cls = f' class="{class_name}"' if class_name else ""
    return "<ul{}>{}</ul>".format(cls, "".join(f"<li>{text(item)}</li>" for item in items))


def trend_symbol(value: str) -> str:
    return {"up": "↑", "up2": "↑↑", "flat": "→", "down": "↓"}.get(str(value), str(value or ""))


def radar_chart(abilities: list[dict[str, Any]]) -> str:
    if not abilities:
        return ""
    cx = cy = 150
    radius = 104
    max_score = 5
    points = []
    axis = []
    labels = []
    count = len(abilities)
    for index, ability in enumerate(abilities):
        angle = -math.pi / 2 + index * 2 * math.pi / count
        score = float(ability.get("score", 0))
        x = cx + math.cos(angle) * radius * score / max_score
        y = cy + math.sin(angle) * radius * score / max_score
        points.append(f"{x:.1f},{y:.1f}")
        ax = cx + math.cos(angle) * radius
        ay = cy + math.sin(angle) * radius
        axis.append(f'<line x1="{cx}" y1="{cy}" x2="{ax:.1f}" y2="{ay:.1f}" />')
        lx = cx + math.cos(angle) * (radius + 24)
        ly = cy + math.sin(angle) * (radius + 24)
        labels.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" dominant-baseline="middle">{text(ability.get("name"))}</text>'
        )
    rings = []
    for step in range(1, max_score + 1):
        ring = []
        for index in range(count):
            angle = -math.pi / 2 + index * 2 * math.pi / count
            ring.append(
                f"{cx + math.cos(angle) * radius * step / max_score:.1f},"
                f"{cy + math.sin(angle) * radius * step / max_score:.1f}"
            )
        rings.append(f'<polygon points="{" ".join(ring)}" />')
    return f"""<svg class="radar" viewBox="0 0 300 300" role="img" aria-label="能力发展雷达图">
  <g class="radar-grid">{"".join(rings)}{"".join(axis)}</g>
  <polygon class="radar-shape" points="{" ".join(points)}" />
  <g class="radar-labels">{"".join(labels)}</g>
</svg>"""


def render_html(data: dict[str, Any]) -> str:
    metrics = data.get("metrics") or []
    abilities = data.get("abilities") or []
    themes = data.get("curiosity_themes") or []
    works = data.get("works") or []
    suggestions = data.get("suggestions") or []
    metric_cards = "".join(
        f'<article class="metric"><span>{text(item.get("label"))}</span><strong>{text(item.get("value"))}</strong><small>{text(item.get("note"))}</small></article>'
        for item in metrics
    )
    def ability_value(item: dict[str, Any], key: str) -> str:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
        numeric = item.get("score" if key == "current" else key)
        if isinstance(numeric, (int, float)):
            filled = max(0, min(5, round(float(numeric))))
            return "★" * filled + "☆" * (5 - filled)
        return ""

    def ability_trend(item: dict[str, Any]) -> str:
        explicit = item.get("trend")
        if explicit:
            return trend_symbol(str(explicit))
        try:
            delta = float(item.get("score", 0)) - float(item.get("previous", 0))
        except (TypeError, ValueError):
            return ""
        if delta >= 1.5:
            return "↑↑"
        if delta > 0:
            return "↑"
        if delta < 0:
            return "↓"
        return "→"

    ability_rows = "".join(
        f'<tr><td>{text(item.get("name"))}</td><td>{text(ability_value(item, "current"))}</td><td>{text(ability_value(item, "previous"))}</td><td>{text(ability_trend(item))}</td></tr>'
        for item in abilities
    )
    theme_cards = "".join(
        f'<article class="theme"><strong>{idx}. {text(item.get("name"))}</strong><span>{text(item.get("count"))} 次</span><p>{text(item.get("insight"))}</p></article>'
        for idx, item in enumerate(themes, 1)
    )
    work_cards = "".join(
        f'<article class="work"><strong>{text(item.get("title"))}</strong><span>{text(item.get("type"))}</span><p>{text(item.get("highlight"))}</p></article>'
        for item in works
    )
    suggestion_cards = "".join(
        f'<article class="suggestion"><span>{text(item.get("label") or item.get("title"))}</span><p>{text(item.get("text") or item.get("body"))}</p></article>'
        for item in suggestions
    )

    def milestone_text(item: Any) -> str:
        if isinstance(item, dict):
            return " · ".join(str(part) for part in (item.get("date"), item.get("title"), item.get("detail")) if part)
        return str(item)

    milestones = list_items([milestone_text(item) for item in (data.get("milestones") or [])], class_name="evidence-list")
    expression = list_items(data.get("expression_progress") or [], class_name="evidence-list")
    quote = data.get("quote") or {}
    if isinstance(quote, str):
        quote = {"text": quote}
    elif not quote and data.get("hero_quote"):
        quote = {"text": data.get("hero_quote"), "source": "第8周互动摘录"}
    profile = data.get("profile_update_summary") or {}
    if isinstance(profile, list):
        profile = {"summary": "；".join(str(item).strip() for item in profile if str(item).strip())}
    elif isinstance(profile, str):
        profile = {"summary": profile}
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{text(data.get("title"))}</title>
<style>
@page {{ size: A4; margin: 10mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: "Microsoft YaHei", "Noto Sans SC", "Source Han Sans SC", Arial, sans-serif; color: #20312f; background: #eef5f3; }}
.report {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
.hero {{ padding: 30px; border-radius: 22px; color: white; background: linear-gradient(135deg, #165b57, #347b68 54%, #e19f4d); }}
.hero h1 {{ margin: 0 0 10px; font-size: 34px; letter-spacing: 0; }}
.hero p {{ max-width: 780px; margin: 8px 0 0; font-size: 17px; line-height: 1.7; }}
.period {{ display: inline-flex; gap: 10px; align-items: center; padding: 7px 12px; border-radius: 999px; background: rgba(255,255,255,.18); font-size: 14px; }}
.metrics {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin: 18px 0; }}
.metric, .panel, .theme, .work, .suggestion {{ border: 1px solid rgba(23,88,82,.14); border-radius: 16px; background: rgba(255,255,255,.94); box-shadow: 0 12px 34px rgba(25,70,62,.09); }}
.metric {{ padding: 17px; }}
.metric span, .work span, .suggestion span {{ color: #55736e; font-size: 13px; }}
.metric strong {{ display: block; margin: 8px 0 4px; color: #174f4b; font-size: 30px; }}
.metric small {{ color: #72847f; }}
.grid {{ display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, .95fr); gap: 18px; }}
.panel {{ margin: 0 0 18px; padding: 22px; }}
.panel h2 {{ margin: 0 0 16px; color: #174f4b; font-size: 22px; }}
.radar-wrap {{ display: grid; grid-template-columns: 330px 1fr; gap: 18px; align-items: center; }}
.radar-grid polygon {{ fill: none; stroke: #c8d8d3; stroke-width: 1; }}
.radar-grid line {{ stroke: #d5e1dd; stroke-width: 1; }}
.radar-shape {{ fill: rgba(225,159,77,.35); stroke: #d58d30; stroke-width: 3; }}
.radar-labels text {{ fill: #315d58; font-size: 13px; font-weight: 700; }}
table {{ width: 100%; border-collapse: collapse; overflow: hidden; border-radius: 12px; }}
td, th {{ padding: 11px 12px; border-bottom: 1px solid #e3ede9; text-align: left; }}
th {{ color: #55736e; font-size: 13px; }}
.themes, .works, .suggestions {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }}
.theme, .work, .suggestion {{ padding: 16px; box-shadow: none; }}
.theme strong, .work strong {{ display: block; color: #174f4b; }}
.theme span {{ display: inline-block; margin: 8px 0; padding: 5px 9px; border-radius: 999px; color: #8a5514; background: #fff0d8; font-weight: 700; }}
.theme p, .work p, .suggestion p {{ margin: 8px 0 0; line-height: 1.65; }}
.quote {{ padding: 18px 20px; border-left: 5px solid #e19f4d; border-radius: 14px; background: #fff8eb; font-size: 18px; line-height: 1.7; }}
.quote small {{ display: block; margin-top: 8px; color: #75614a; font-size: 13px; }}
.evidence-list {{ margin: 0; padding-left: 20px; line-height: 1.8; }}
.next {{ border: 1px solid #b9dccf; background: #e6f5ef; }}
.profile {{ color: #456761; font-size: 14px; line-height: 1.8; }}
@media (max-width: 860px) {{
  .report {{ padding: 14px; }}
  .metrics, .grid, .radar-wrap, .themes, .works, .suggestions {{ grid-template-columns: 1fr; }}
  .hero h1 {{ font-size: 28px; }}
}}
@media print {{
  body {{ background: white; }}
  .report {{ padding: 0; }}
  .panel, .metric {{ box-shadow: none; }}
}}
</style>
</head>
<body>
<main class="report">
  <section class="hero">
    <div class="period">{text(data.get("period"))}</div>
    <h1>{text(data.get("title"))}</h1>
    <p>{text(data.get("summary"))}</p>
  </section>
  <section class="metrics">{metric_cards}</section>
  <div class="grid">
    <section class="panel">
      <h2>能力发展雷达图</h2>
      <div class="radar-wrap">{radar_chart(abilities)}<table><thead><tr><th>维度</th><th>本月</th><th>上月</th><th>趋势</th></tr></thead><tbody>{ability_rows}</tbody></table></div>
    </section>
    <section class="panel">
      <h2>本月童言精选</h2>
      <div class="quote">“{text(quote.get("text"))}”<small>{text(quote.get("insight"))}</small></div>
    </section>
  </div>
  <section class="panel">
    <h2>TOP3 好奇主题</h2>
    <div class="themes">{theme_cards}</div>
  </section>
  <section class="panel">
    <h2>创作成就</h2>
    <div class="works">{work_cards}</div>
  </section>
  <div class="grid">
    <section class="panel">
      <h2>成长里程碑</h2>
      {milestones}
    </section>
    <section class="panel">
      <h2>表达力进步</h2>
      {expression}
    </section>
  </div>
  <section class="panel">
    <h2>个性化建议</h2>
    <div class="suggestions">{suggestion_cards}</div>
  </section>
  <section class="panel next">
    <h2>下周内容预告</h2>
    <p>{text(data.get("next_theme"))}</p>
  </section>
  <section class="panel">
    <h2>画像更新摘要</h2>
    <p class="profile">{text(profile.get("summary"))}</p>
  </section>
</main>
</body>
</html>"""


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
    with tempfile.TemporaryDirectory(prefix="growth-report-chrome-", dir=str(pdf_path.parent)) as profile:
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
    parser = argparse.ArgumentParser(description="Render child growth trajectory report JSON to HTML/PDF.")
    parser.add_argument("--data", required=True, help="report-data.json path under /output/")
    parser.add_argument("--output-dir", required=True, help="Report output directory under /output/")
    parser.add_argument("--pdf", action="store_true", help="Also render PDF with local Chrome/Edge.")
    args = parser.parse_args()
    try:
        data_path = resolve_artifact_path(args.data)
        output_dir = resolve_artifact_path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        data = json.loads(data_path.read_text(encoding="utf-8-sig"))
        slug = str(data.get("slug") or "growth-report")
        html_path = output_dir / "index.html"
        pdf_path = output_dir / f"{slug}.pdf"
        html_path.write_text(render_html(data), encoding="utf-8")
        if args.pdf:
            print_pdf(html_path, pdf_path)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Growth report render failed: {exc}", file=sys.stderr)
        return 1
    print(f"HTML saved to: {html_path}")
    if args.pdf:
        print(f"PDF saved to: {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
