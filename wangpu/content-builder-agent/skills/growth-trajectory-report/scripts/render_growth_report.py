#!/usr/bin/env python3
"""Render a growth trajectory report JSON to HTML and optional PDF.

V2 supports:
- evidence_image per curiosity_themes entry
- cover_image / link per works entry
- exploration_photos: list of {image, caption, date, theme}
- artifact_gallery: list of {type, title, cover_image, link}
- highlights: list of {type, content, source, date}
- expression_progress: list of strings
- suggestions as {tag, title, body}
- next_theme.starter_questions: list of strings

Image paths in JSON may be workspace-relative ("history/..." or
"roadshow-final-products/..."). They are resolved to absolute file://
URIs at render time so double-clicking the HTML still shows images.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import math
import mimetypes
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
REPORTS_ROOT = Path(os.environ.get("CONTENT_BUILDER_REPORTS_DIR", OUTPUT_ROOT / "reports")).resolve()
CHROME_CANDIDATES = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
]


def resolve_artifact_path(raw_path: str) -> Path:
    raw = str(raw_path).strip().strip("\"'")
    normalized = raw.replace("\\", "/")
    if normalized == "/output/growth-report" or normalized.startswith("/output/growth-report/"):
        resolved = (REPORTS_ROOT / "growth-report" / normalized.removeprefix("/output/growth-report").lstrip("/")).resolve()
        allowed_root = (REPORTS_ROOT / "growth-report").resolve()
    elif normalized == "/output/reports" or normalized.startswith("/output/reports/"):
        resolved = (REPORTS_ROOT / normalized.removeprefix("/output/reports").lstrip("/")).resolve()
        allowed_root = REPORTS_ROOT.resolve()
    elif normalized == "/output" or normalized.startswith("/output/"):
        resolved = (OUTPUT_ROOT / normalized.removeprefix("/output").lstrip("/")).resolve()
        allowed_root = OUTPUT_ROOT.resolve()
    elif normalized == "/reports" or normalized.startswith("/reports/"):
        resolved = (REPORTS_ROOT / normalized.removeprefix("/reports").lstrip("/")).resolve()
        allowed_root = REPORTS_ROOT.resolve()
    elif normalized == "output/growth-report" or normalized.startswith("output/growth-report/"):
        resolved = (REPORTS_ROOT / "growth-report" / normalized.removeprefix("output/growth-report").lstrip("/")).resolve()
        allowed_root = (REPORTS_ROOT / "growth-report").resolve()
    elif normalized == "output/reports" or normalized.startswith("output/reports/"):
        resolved = (REPORTS_ROOT / normalized.removeprefix("output/reports").lstrip("/")).resolve()
        allowed_root = REPORTS_ROOT.resolve()
    elif normalized == "output" or normalized.startswith("output/"):
        resolved = (OUTPUT_ROOT / normalized.removeprefix("output").lstrip("/")).resolve()
        allowed_root = OUTPUT_ROOT.resolve()
    elif normalized == "reports" or normalized.startswith("reports/"):
        resolved = (REPORTS_ROOT / normalized.removeprefix("reports").lstrip("/")).resolve()
        allowed_root = REPORTS_ROOT.resolve()
    elif normalized == "growth-report" or normalized.startswith("growth-report/"):
        resolved = (REPORTS_ROOT / normalized).resolve()
        allowed_root = REPORTS_ROOT.resolve()
    else:
        path = Path(raw)
        resolved = path.resolve() if path.is_absolute() else (WORKSPACE_ROOT / path).resolve()
        allowed_root = OUTPUT_ROOT.resolve()
    roadshow_root = (WORKSPACE_ROOT / "roadshow-final-products").resolve()
    if not (
        resolved == allowed_root
        or allowed_root in resolved.parents
        or resolved == roadshow_root
        or roadshow_root in resolved.parents
    ):
        raise ValueError(f"Report artifact path must be under /output/, /reports/, or legacy roadshow-final-products/: {raw_path}")
    return resolved


def text(value: Any) -> str:
    return html.escape(str(value or ""))


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def fallback_cover_path(path: Path) -> Path | None:
    """Find a renamed cover image when old data still points at cover.png."""
    if path.name.lower() != "cover.png" or not path.parent.is_dir():
        return None
    for candidate in sorted(path.parent.iterdir(), key=lambda item: item.name):
        if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTENSIONS:
            return candidate.resolve()
    return None


def resolve_image_path(raw_path: str) -> Path | None:
    """Resolve an image path that may be workspace-relative."""
    if not raw_path:
        return None
    raw = str(raw_path).strip().strip("\"'")
    p = Path(raw)
    if p.is_absolute() and p.exists():
        return p.resolve()
    # workspace-relative
    candidate = (WORKSPACE_ROOT / raw).resolve()
    if candidate.exists():
        return candidate
    fallback = fallback_cover_path(candidate)
    if fallback:
        return fallback
    return None


def image_to_data_uri(path: Path) -> str:
    """Embed an image as data: URI. Used when local file:// links may be blocked."""
    try:
        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "image/png"
        data = path.read_bytes()
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    except OSError:
        return ""


def image_src(raw_path: str, *, prefer_data: bool = False) -> str:
    """Return HTML <img> src for a path. Empty when missing."""
    p = resolve_image_path(raw_path)
    if not p:
        return ""
    if prefer_data:
        data = image_to_data_uri(p)
        if data:
            return data
    return p.as_uri()


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


def render_html(data: dict[str, Any], *, prefer_data: bool = False) -> str:
    metrics = data.get("metrics") or []
    abilities = data.get("abilities") or []
    themes = data.get("curiosity_themes") or []
    works = data.get("works") or []
    suggestions = data.get("suggestions") or []
    expression = data.get("expression_progress") or []
    exploration_photos = data.get("exploration_photos") or []
    artifact_gallery = data.get("artifact_gallery") or []
    highlights = data.get("highlights") or []
    next_theme = data.get("next_theme") or {}
    profile = data.get("profile_update_summary") or {}
    if isinstance(profile, list):
        profile = {"summary": "；".join(str(item).strip() for item in profile if str(item).strip())}
    elif isinstance(profile, str):
        profile = {"summary": profile}

    quote = data.get("quote") or {}
    if isinstance(quote, str):
        quote = {"text": quote}
    elif not quote and data.get("hero_quote"):
        quote = {"text": data.get("hero_quote"), "source": "本月互动摘录"}

    metric_cards = "".join(
        f'<article class="metric"><span>{text(item.get("label"))}</span><strong>{text(item.get("value"))}{text(item.get("unit") or "")}</strong><small>{text(item.get("note"))}</small></article>'
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

    def ability_change(item: dict[str, Any]) -> str:
        try:
            delta = float(item.get("score", 0)) - float(item.get("previous", 0))
        except (TypeError, ValueError):
            return ""
        if delta > 0:
            return f"+{delta:.1f}"
        if delta < 0:
            return f"{delta:.1f}"
        return "0.0"

    ability_rows = "".join(
        f'<tr><td>{text(item.get("name"))}</td><td>{text(ability_value(item, "current"))}</td><td>{text(ability_change(item))}</td><td>{text(ability_trend(item))}</td></tr>'
        for item in abilities
    )
    methodology = data.get("methodology") or [
        {
            "dimension": item.get("name"),
            "signals": item.get("evidence") or "来自对话、照片探索、作品选择和亲子共创记录的综合证据。",
            "scoring": "1-5 分：出现频率、主动性、迁移跨度和表达完整度共同决定；只把稳定重复的证据写入长期画像。",
        }
        for item in abilities
    ]
    methodology_cards = "".join(
        f'<article class="method-card">'
        f'<strong>{text(item.get("dimension") or item.get("title"))}</strong>'
        f'<p>{text(item.get("signals") or item.get("evidence"))}</p>'
        f'<small>{text(item.get("scoring") or item.get("method"))}</small>'
        f'</article>'
        for item in methodology
    )

    # 好奇主题卡（含图）
    theme_cards = []
    for idx, item in enumerate(themes, 1):
        img_src = image_src(item.get("evidence_image", ""), prefer_data=prefer_data)
        evidence = text(item.get("evidence_note", ""))
        if img_src:
            figure = (
                f'<figure class="theme-figure">'
                f'<img src="{html.escape(img_src, quote=True)}" alt="{text(item.get("name"))} 主题示例" loading="lazy">'
                f'<figcaption>{evidence}</figcaption>'
                f'</figure>'
            )
        else:
            figure = f'<div class="theme-figure theme-figure--placeholder">暂无图片 · 文字证据已记录</div>'
        theme_cards.append(
            f'<article class="theme">'
            f'<header><strong>{idx}. {text(item.get("name"))}</strong><span class="badge">{text(item.get("count"))} 次</span></header>'
            f'<p class="theme-insight">{text(item.get("insight"))}</p>'
            f'{figure}'
            f'</article>'
        )
    themes_markup = "".join(theme_cards)

    # 创作成就卡（封面 + 文字 + 链接）
    work_cards = []
    for item in works:
        cover = image_src(item.get("cover_image", ""), prefer_data=prefer_data)
        link = item.get("link")
        if cover:
            cover_html = f'<img src="{html.escape(cover, quote=True)}" alt="{text(item.get("title"))} 封面" loading="lazy">'
        else:
            cover_html = '<div class="work-cover work-cover--placeholder">暂无封面</div>'
        if link and not prefer_data:
            cover_html = f'<a class="work-cover" href="{html.escape(str(link))}" target="_blank" rel="noopener">{cover_html}</a>'
        elif cover_html.startswith('<a'):
            pass
        else:
            cover_html = f'<div class="work-cover">{cover_html}</div>'
        work_cards.append(
            f'<article class="work">'
            f'{cover_html}'
            f'<div class="work-body">'
            f'<strong>{text(item.get("title"))}</strong>'
            f'<span class="badge">{text(item.get("type"))}</span>'
            f'<p>{text(item.get("highlight"))}</p>'
            f'</div>'
            f'</article>'
        )
    works_markup = "".join(work_cards)

    # 探险相册
    photo_cards = []
    for item in exploration_photos:
        src = image_src(item.get("image", ""), prefer_data=prefer_data)
        if not src:
            continue
        photo_cards.append(
            f'<figure class="photo-card">'
            f'<img src="{html.escape(src, quote=True)}" alt="{text(item.get("caption"))}" loading="lazy">'
            f'<figcaption><span class="photo-theme">{text(item.get("theme", ""))}</span>'
            f'<span class="photo-caption">{text(item.get("caption"))}</span>'
            f'<span class="photo-date">{text(item.get("date", ""))}</span></figcaption>'
            f'</figure>'
        )
    photos_markup = "".join(photo_cards)

    # 产物走廊
    gallery_cards = []
    for item in artifact_gallery:
        cover = image_src(item.get("cover_image", ""), prefer_data=prefer_data)
        link = item.get("link")
        if cover:
            inner = f'<img src="{html.escape(cover, quote=True)}" alt="{text(item.get("title"))}" loading="lazy">'
        else:
            inner = '<div class="work-cover--placeholder">暂无封面</div>'
        if link and not prefer_data:
            inner = f'<a href="{html.escape(str(link))}" target="_blank" rel="noopener">{inner}</a>'
        gallery_cards.append(
            f'<article class="gallery-card">'
            f'<div class="gallery-cover">{inner}</div>'
            f'<div class="gallery-meta"><span class="badge">{text(item.get("type"))}</span>'
            f'<strong>{text(item.get("title"))}</strong></div>'
            f'</article>'
        )
    gallery_markup = "".join(gallery_cards)

    # 童言 / 亮点 时间线
    timeline_items = []
    for item in highlights:
        type_text = text(item.get("type", ""))
        content = text(item.get("content", ""))
        source = text(item.get("source", ""))
        date = text(item.get("date", ""))
        timeline_items.append(
            f'<li class="timeline-item">'
            f'<div class="timeline-marker" data-type="{type_text}"></div>'
            f'<div class="timeline-body">'
            f'<div class="timeline-meta"><span class="badge">{type_text}</span><span>{date}</span><span>{source}</span></div>'
            f'<p>{content}</p>'
            f'</div></li>'
        )
    timeline_markup = f'<ol class="timeline">{"".join(timeline_items)}</ol>' if timeline_items else ""

    # 里程碑
    def milestone_text(item: Any) -> str:
        if isinstance(item, dict):
            return " · ".join(str(part) for part in (item.get("date"), item.get("title"), item.get("detail")) if part)
        return str(item)

    milestones = list_items([milestone_text(item) for item in (data.get("milestones") or [])], class_name="evidence-list")

    # 表达力进步
    expression_html = "".join(
        f'<li><span class="dot-bullet"></span><span>{text(item)}</span></li>'
        for item in expression
    ) if expression else '<li class="empty">暂无更多表达力记录</li>'

    # 建议卡
    suggestion_cards = []
    for item in suggestions:
        tag = text(item.get("tag", ""))
        title = text(item.get("title", ""))
        body = text(item.get("body", ""))
        suggestion_cards.append(
            f'<article class="suggestion">'
            f'<span class="chip">{tag}</span>'
            f'<strong>{title}</strong>'
            f'<p>{body}</p>'
            f'</article>'
        )
    suggestions_markup = "".join(suggestion_cards)

    # 下周预告
    next_title = text(next_theme.get("title", ""))
    next_why = text(next_theme.get("why", ""))
    next_questions = next_theme.get("starter_questions") or []
    next_q_html = "".join(
        f'<li><span class="q-num">{i+1}</span><span>{text(q)}</span></li>'
        for i, q in enumerate(next_questions)
    )

    profile_items = []
    for line in str(profile.get("summary", "")).split("；"):
        line = line.strip()
        if line:
            profile_items.append(f'<li><span class="dot-bullet"></span><span>{text(line)}</span></li>')
    profile_html = "".join(profile_items) or '<li class="empty">暂无画像更新</li>'

    # Hero
    child_name = text(data.get("child_name", ""))
    period_text = text(data.get("period", ""))

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{text(data.get("title"))}</title>
<style>
@page {{ size: A4; margin: 10mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; font-family: "Microsoft YaHei", "Noto Sans SC", "Source Han Sans SC", Arial, sans-serif; color: #20312f; background: #eef5f3; -webkit-font-smoothing: antialiased; }}
.report {{ max-width: 1220px; margin: 0 auto; padding: 28px; }}
.hero {{ position: relative; padding: 40px 42px; border-radius: 18px; color: white; background: linear-gradient(90deg, rgba(65,170,200,.16) 1px, transparent 1px) 0 0 / 42px 42px, linear-gradient(135deg, #102a32 0%, #173f45 56%, #2d7468 100%); overflow: hidden; box-shadow: 0 22px 58px rgba(16,42,50,.24); }}
.hero::after {{ content: "水滴 -> 云朵 -> 雨滴 -> 花园 -> 阳光"; position: absolute; right: 30px; bottom: 24px; color: rgba(255,255,255,.18); font-size: 15px; font-weight: 800; letter-spacing: .08em; }}
.hero .period {{ display: inline-flex; gap: 8px; align-items: center; padding: 6px 14px; border-radius: 999px; background: rgba(255,255,255,.18); font-size: 13px; letter-spacing: .08em; }}
.hero h1 {{ margin: 12px 0 8px; font-size: 36px; letter-spacing: 0; }}
.hero .child {{ display: inline-block; margin-left: 12px; padding: 4px 10px; border-radius: 8px; background: rgba(255,255,255,.16); font-size: 14px; letter-spacing: .04em; }}
.hero p {{ max-width: 760px; margin: 8px 0 0; font-size: 17px; line-height: 1.7; }}
.hero .mood {{ display: inline-flex; gap: 6px; margin-top: 14px; padding: 6px 12px; border-radius: 999px; background: rgba(255,255,255,.14); font-size: 12px; letter-spacing: .12em; }}
.metrics {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin: 22px 0; }}
.metric, .panel, .theme, .work, .suggestion, .gallery-card, .photo-card, .method-card {{ border: 1px solid rgba(23,88,82,.14); border-radius: 10px; background: rgba(255,255,255,.96); box-shadow: 0 12px 34px rgba(25,70,62,.09); }}
.metric {{ padding: 18px; }}
.metric span {{ color: #55736e; font-size: 13px; }}
.metric strong {{ display: block; margin: 8px 0 4px; color: #174f4b; font-size: 30px; }}
.metric small {{ color: #72847f; }}
.grid {{ display: grid; grid-template-columns: minmax(0, 1.05fr) minmax(0, .95fr); gap: 20px; }}
.panel {{ margin: 0 0 20px; padding: 24px; }}
.panel h2 {{ margin: 0 0 16px; color: #174f4b; font-size: 22px; letter-spacing: .02em; }}
.panel h2 .accent-bar {{ display: inline-block; width: 4px; height: 18px; margin-right: 8px; background: linear-gradient(180deg, #347b68, #e19f4d); border-radius: 2px; vertical-align: -3px; }}
.radar-wrap {{ display: grid; grid-template-columns: 320px 1fr; gap: 18px; align-items: center; }}
.radar-grid polygon {{ fill: none; stroke: #c8d8d3; stroke-width: 1; }}
.radar-grid line {{ stroke: #d5e1dd; stroke-width: 1; }}
.radar-shape {{ fill: rgba(225,159,77,.35); stroke: #d58d30; stroke-width: 3; }}
.radar-labels text {{ fill: #315d58; font-size: 13px; font-weight: 700; }}
table {{ width: 100%; border-collapse: collapse; overflow: hidden; border-radius: 12px; }}
td, th {{ padding: 11px 12px; border-bottom: 1px solid #e3ede9; text-align: left; }}
th {{ color: #55736e; font-size: 12px; letter-spacing: .04em; text-transform: uppercase; }}
.method-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }}
.method-card {{ padding: 16px; background: #f8fcfa; }}
.method-card strong {{ color: #174f4b; font-size: 15px; }}
.method-card p {{ margin: 8px 0; color: #2c4642; font-size: 13px; line-height: 1.65; }}
.method-card small {{ display: block; color: #6f807b; font-size: 12px; line-height: 1.55; }}
.themes {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }}
.theme {{ padding: 18px; display: flex; flex-direction: column; gap: 10px; }}
.theme header {{ display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }}
.theme strong {{ color: #174f4b; font-size: 17px; }}
.theme .badge {{ display: inline-block; padding: 4px 9px; border-radius: 999px; color: #8a5514; background: #fff0d8; font-weight: 700; font-size: 12px; }}
.theme-insight {{ margin: 0; color: #2c4642; line-height: 1.6; font-size: 14px; }}
.theme-figure {{ margin: 0; border-radius: 12px; overflow: hidden; background: #f6efe2; }}
.theme-figure img {{ display: block; width: 100%; aspect-ratio: 4 / 3; object-fit: cover; }}
.theme-figure figcaption {{ padding: 8px 10px; font-size: 12px; color: #4d5a55; line-height: 1.5; background: #fffaf0; }}
.theme-figure--placeholder {{ padding: 22px 12px; text-align: center; color: #8b8576; font-size: 13px; }}
.works {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }}
.work {{ padding: 0; display: flex; flex-direction: column; overflow: hidden; }}
.work-cover {{ display: block; aspect-ratio: 1 / 1; overflow: hidden; background: #f3ece0; }}
.work-cover img {{ width: 100%; height: 100%; object-fit: cover; transition: transform .4s ease; }}
.work:hover .work-cover img {{ transform: scale(1.04); }}
.work-cover--placeholder {{ display: flex; align-items: center; justify-content: center; color: #8b8576; font-size: 13px; }}
.work-body {{ padding: 14px 16px 18px; }}
.work-body strong {{ display: block; color: #174f4b; font-size: 15px; margin-bottom: 4px; }}
.work-body .badge {{ display: inline-block; padding: 3px 8px; border-radius: 999px; color: #347b68; background: #e2f0ec; font-size: 11px; margin-bottom: 6px; }}
.work-body p {{ margin: 6px 0 0; line-height: 1.55; font-size: 13px; color: #2c4642; }}
.gallery-row {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }}
.gallery-card {{ overflow: hidden; display: flex; flex-direction: column; }}
.gallery-cover {{ aspect-ratio: 4 / 3; overflow: hidden; background: #f3ece0; }}
.gallery-cover img {{ width: 100%; height: 100%; object-fit: cover; transition: transform .4s ease; }}
.gallery-card:hover .gallery-cover img {{ transform: scale(1.05); }}
.gallery-meta {{ padding: 10px 12px 12px; display: flex; flex-direction: column; gap: 4px; }}
.gallery-meta strong {{ color: #174f4b; font-size: 14px; }}
.gallery-meta .badge {{ display: inline-block; padding: 3px 8px; border-radius: 999px; color: #347b68; background: #e2f0ec; font-size: 11px; align-self: flex-start; }}
.exploration {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }}
.photo-card {{ overflow: hidden; display: flex; flex-direction: column; }}
.photo-card img {{ width: 100%; aspect-ratio: 1 / 1; object-fit: cover; }}
.photo-card figcaption {{ padding: 8px 10px 10px; display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #2c4642; }}
.photo-theme {{ display: inline-block; padding: 2px 7px; border-radius: 6px; color: #8a5514; background: #fff0d8; font-size: 11px; align-self: flex-start; }}
.photo-caption {{ color: #2c4642; }}
.photo-date {{ color: #8b8b80; font-size: 11px; }}
.timeline {{ list-style: none; padding: 0; margin: 0; position: relative; }}
.timeline::before {{ content: ""; position: absolute; left: 9px; top: 4px; bottom: 4px; width: 2px; background: linear-gradient(180deg, #347b68, #e19f4d); border-radius: 1px; }}
.timeline-item {{ position: relative; padding: 0 0 14px 32px; }}
.timeline-marker {{ position: absolute; left: 0; top: 4px; width: 20px; height: 20px; border-radius: 50%; background: #fff; border: 3px solid #347b68; box-shadow: 0 0 0 3px rgba(52,123,104,.18); }}
.timeline-marker[data-type="童言"] {{ border-color: #d58d30; box-shadow: 0 0 0 3px rgba(213,141,48,.18); }}
.timeline-marker[data-type="创作"] {{ border-color: #347b68; box-shadow: 0 0 0 3px rgba(52,123,104,.18); }}
.timeline-marker[data-type="里程碑"] {{ border-color: #5b3aa0; box-shadow: 0 0 0 3px rgba(91,58,160,.18); }}
.timeline-meta {{ display: flex; gap: 8px; align-items: center; font-size: 12px; color: #55736e; }}
.timeline-meta .badge {{ display: inline-block; padding: 2px 7px; border-radius: 6px; color: #fff; background: #347b68; font-size: 11px; }}
.timeline p {{ margin: 4px 0 0; line-height: 1.65; color: #20312f; font-size: 14px; }}
.evidence-list {{ margin: 0; padding-left: 20px; line-height: 1.85; }}
.evidence-list li {{ margin-bottom: 4px; }}
.suggestions {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px; }}
.suggestion {{ padding: 18px; }}
.suggestion .chip {{ display: inline-block; padding: 3px 8px; border-radius: 6px; color: #347b68; background: #e2f0ec; font-size: 11px; margin-bottom: 6px; }}
.suggestion strong {{ display: block; color: #174f4b; margin-bottom: 4px; font-size: 15px; }}
.suggestion p {{ margin: 6px 0 0; line-height: 1.6; font-size: 13px; color: #2c4642; }}
.dot-bullet {{ display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #347b68; margin: 0 8px 2px 0; vertical-align: middle; }}
.expression, .profile {{ list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }}
.expression li, .profile li {{ display: flex; align-items: flex-start; padding: 8px 10px; background: #fff; border-radius: 10px; border: 1px solid rgba(23,88,82,.1); font-size: 14px; line-height: 1.6; color: #20312f; }}
.next-questions {{ list-style: none; padding: 0; margin: 12px 0 0; display: flex; flex-direction: column; gap: 8px; }}
.next-questions li {{ display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; background: #fff8eb; border-radius: 12px; border-left: 3px solid #e19f4d; font-size: 14px; line-height: 1.6; color: #20312f; }}
.q-num {{ display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: 50%; background: #e19f4d; color: #fff; font-weight: 700; font-size: 12px; flex-shrink: 0; }}
.empty {{ color: #8b8576; font-style: italic; }}
@media (max-width: 860px) {{
  .report {{ padding: 16px; }}
  .hero {{ padding: 22px; }}
  .hero h1 {{ font-size: 26px; }}
  .metrics, .grid, .radar-wrap, .themes, .works, .suggestions, .gallery-row {{ grid-template-columns: 1fr; }}
  .exploration {{ grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); }}
}}
@media print {{
  body {{ background: white; }}
  .report {{ padding: 0; }}
  .panel, .metric, .theme, .work, .suggestion, .gallery-card, .photo-card {{ box-shadow: none; }}
  .work-cover, .gallery-cover, .theme-figure img, .photo-card img {{ break-inside: avoid; }}
}}
</style>
</head>
<body>
<main class="report">
  <section class="hero">
    <div class="period">{period_text}</div>
    <h1>{text(data.get("title"))} <span class="child">{child_name} 的成长档案</span></h1>
    <p>{text(data.get("summary"))}</p>
    <div class="mood">本月关键词 · 水的变化 · 故事化解释 · 多模态产出</div>
  </section>
  <section class="metrics">{metric_cards}</section>

  <div class="grid">
    <section class="panel">
      <h2><span class="accent-bar"></span>能力发展雷达图</h2>
      <div class="radar-wrap">{radar_chart(abilities)}<table><thead><tr><th>维度</th><th>本月</th><th>变化</th><th>趋势</th></tr></thead><tbody>{ability_rows}</tbody></table></div>
    </section>
    <section class="panel">
      <h2><span class="accent-bar"></span>本月童言精选</h2>
      <div class="quote" style="padding:18px 20px;border-left:5px solid #e19f4d;border-radius:14px;background:#fff8eb;font-size:18px;line-height:1.7;">“{text(quote.get("text"))}”<small style="display:block;margin-top:8px;color:#75614a;font-size:13px;">{text(quote.get("source"))}</small></div>
      <h2 style="margin-top:22px;"><span class="accent-bar"></span>亮点时间线</h2>
      {timeline_markup}
    </section>
  </div>

  <section class="panel">
    <h2><span class="accent-bar"></span>指标如何得出</h2>
    <div class="method-grid">{methodology_cards}</div>
  </section>

  <section class="panel">
    <h2><span class="accent-bar"></span>TOP3 好奇主题</h2>
    <div class="themes">{themes_markup}</div>
  </section>

  <section class="panel">
    <h2><span class="accent-bar"></span>创作成就</h2>
    <div class="works">{works_markup}</div>
  </section>

  <section class="panel">
    <h2><span class="accent-bar"></span>作品走廊</h2>
    <div class="gallery-row">{gallery_markup}</div>
  </section>

  <section class="panel">
    <h2><span class="accent-bar"></span>探险相册 · 来自生活的小观察</h2>
    <div class="exploration">{photos_markup}</div>
  </section>

  <div class="grid">
    <section class="panel">
      <h2><span class="accent-bar"></span>成长里程碑</h2>
      {milestones}
    </section>
    <section class="panel">
      <h2><span class="accent-bar"></span>表达力进步</h2>
      <ul class="expression">{expression_html}</ul>
    </section>
  </div>

  <section class="panel">
    <h2><span class="accent-bar"></span>个性化建议</h2>
    <div class="suggestions">{suggestions_markup}</div>
  </section>

  <section class="panel" style="border:1px solid #b9dccf;background:#e6f5ef;">
    <h2><span class="accent-bar"></span>下周内容预告 · {next_title}</h2>
    <p style="margin:8px 0 0;line-height:1.7;color:#2c4642;">{next_why}</p>
    <ol class="next-questions">{next_q_html}</ol>
  </section>

  <section class="panel">
    <h2><span class="accent-bar"></span>画像更新摘要</h2>
    <ul class="profile">{profile_html}</ul>
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
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(f"Chrome PDF rendering failed ({completed.returncode}): {details}")
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError(f"Chrome did not produce a non-empty PDF: {pdf_path}")


def display_pdf_filename(data: dict[str, Any]) -> str:
    title = str(data.get("title") or data.get("headline") or data.get("slug") or "growth-report").strip()
    title = re.sub(r"（([^）]+)）", r"-\1", title)
    title = re.sub(r"\(([^)]+)\)", r"-\1", title)
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title)
    filename = re.sub(r"\s+", "", filename).strip(". ")
    return f"{filename or 'growth-report'}.pdf"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render child growth trajectory report JSON to HTML/PDF.")
    parser.add_argument("--data", required=True, help="report-data.json path under /output/ or /reports/")
    parser.add_argument("--output-dir", required=True, help="Report output directory under /reports/ or /output/")
    parser.add_argument("--pdf", action="store_true", help="Also render PDF with local Chrome/Edge.")
    parser.add_argument(
        "--embed-images",
        action="store_true",
        help="Embed images as data: URIs (used for PDF so file:// links work).",
    )
    args = parser.parse_args()
    try:
        data_path = resolve_artifact_path(args.data)
        output_dir = resolve_artifact_path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        data = json.loads(data_path.read_text(encoding="utf-8-sig"))
        html_path = output_dir / "index.html"
        # For PDF: embed images as data: URIs since Chrome blocks file:// loads in headless
        prefer_data = bool(args.embed_images)
        html_path.write_text(render_html(data, prefer_data=prefer_data), encoding="utf-8")
        if args.pdf:
            pdf_path = output_dir / display_pdf_filename(data)
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
