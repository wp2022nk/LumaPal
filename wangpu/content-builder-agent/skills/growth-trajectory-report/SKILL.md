---
name: growth-trajectory-report
description: Generate polished single-child growth trajectory reports for this project and update the long-term child profile. Use when asked for 成长轨迹报告, growth report, curiosity map, parent-facing progress summary, personalized story/game recommendations, or when summarizing child interaction history into a report.
---

# Growth Trajectory Report

Create parent-facing growth reports from this project's chat history, operation events, generated artifacts, and the single long-term child profile.

## Project Memory Contract

- This project serves one child only. Do not introduce child IDs.
- Read `/memory/profile.md` first when it exists.
- Treat `/memory/profile.json` as structured source of truth and `/memory/profile.md` as the agent-readable summary.
- Append evidence to `/memory/events.jsonl` when adding durable observations.
- Keep updates evidence-based and include source, date, and confidence when possible.

## Inputs To Inspect

Use the newest relevant files:

- `/memory/profile.md` and `/memory/profile.json`
- `/memory/events.jsonl`
- `/project/../history/YYYY-MM-DD/conversations/<thread_id>/chat.json`
- `/project/../history/YYYY-MM-DD/conversations/<thread_id>/events.jsonl`
- `/project/../history/YYYY-MM-DD/conversations/<thread_id>/manifest.json`
- `/output/`, `/storybooks/`, `/games/`, and `/reports/` artifacts for the active thread
- User-provided notes, scripts, storybooks, games, drawings, voice transcripts, or reports

If the report is for a roadshow/demo and the user provided a script, use the script as the canonical data source. Legacy flat `history/YYYY-MM-DD/history.json` and `roadshow-final-products/` are manual reference material only; do not write new report files there.

## Report Workflow

1. Collect evidence: questions, creative choices, game behavior, storybook outputs, parent-child interactions, emotion snippets, and produced artifacts.
2. Separate stable patterns from one-off observations. Avoid permanent profile updates from weak evidence.
3. Scan the **photo and artifact** sources (see "Image-aware Report" below) so that every theme, work, and timeline entry has a concrete image to anchor it.
4. Produce `report-data.json` with:
   - overview metrics
   - ability dimensions with current/previous/trend/evidence
   - `methodology` explaining how every radar dimension is derived from chat records, photos, game behavior, story choices, and parent-child co-creation
   - top curiosity themes (each with `evidence_image` + `evidence_note` when possible)
   - favorite question type
   - creative works (each with `cover_image` + optional `link` to the artifact)
   - milestones
   - expression progress
   - personalized suggestions
   - next theme
   - profile updates and evidence
   - `exploration_photos` (8+ items, derived from `history/<date>/uploads/images/*`)
   - `artifact_gallery` (covers from storybooks, games, prior story artifacts)
   - `highlights` (timeline: 童言 / 创作 / 迁移 / 里程碑)
5. Render with `scripts/render_growth_report.py`. Use `--embed-images` when also producing the PDF so Chrome headless can resolve the images via `data:` URIs.
6. Update `/memory/profile.json`, regenerate `/memory/profile.md`, and append `/memory/events.jsonl` when the report yields durable observations.

## Image-aware Report

Current LAN-server snapshots are conversation-scoped. Prefer `history/<YYYY-MM-DD>/conversations/<thread_id>/uploads/...` and `history/<YYYY-MM-DD>/conversations/<thread_id>/artifacts/...`; only inspect `_legacy_archive`, legacy flat `history/<YYYY-MM-DD>/uploads/...`, or `roadshow-final-products/` when the user explicitly asks for legacy material.

Reports should make the child's month tangible by surfacing real photos and artifact covers, not only text metrics. Collect images from these sources:

- `history/<YYYY-MM-DD>/conversations/<thread_id>/uploads/images/*.{jpg,jpeg,png}` — parent-uploaded observations and conversational screenshots. Group by date and choose the most representative 8-12 photos for `exploration_photos`.
- `history/<YYYY-MM-DD>/conversations/<thread_id>/artifacts/storybooks/<slug>/images/*.png` — storybook illustrations and covers.
- `history/<YYYY-MM-DD>/conversations/<thread_id>/artifacts/games/<slug>/index.html` — game links suitable for `artifact_gallery`.
- `history/<YYYY-MM-DD>/conversations/<thread_id>/artifacts/reports/**` — prior parent-facing reports.

When assigning images:

- Each `curiosity_themes[*]` should carry an `evidence_image` path (workspace-relative, e.g. `history/2026-06-08/uploads/images/...`) and a short `evidence_note` describing why it matters.
- Each `works[*]` should carry a `cover_image` and, when available, a `link` to the artifact's `book.html` or `index.html` so the gallery becomes a clickable corridor.
- `exploration_photos[*].image` paths are resolved by the renderer; if an image is missing the photo is silently dropped (no broken `<img>`).
- `artifact_gallery[*]` mirrors the latest set of generated artifacts and is rendered as a 4-column cover wall; provide at least 3 entries.

The renderer also accepts a `slug` field. When supplied, the PDF is written as `<slug>.pdf`; otherwise it defaults to `growth-report.pdf`.

## Visual Standard

Read `references/report-design.md` before designing or revising report layouts.

Reports should be beautiful but immediately understandable:

- Start with a clear monthly/weekly headline and 3-5 key metrics.
- Use grouped cards, a radar chart, trend markers, an "指标如何得出" methodology section, works gallery, milestone narrative, and next-action suggestions.
- Anchor every claim with a real photo (主题/作品/探险相册) and a 童言 timeline so parents see the source of every conclusion.
- Explain radar scores in parent-facing language. Tie each dimension to observable evidence such as question frequency, causal words, cross-scene transfer, role-play choices, artifact output, and parent-child collaboration.
- Keep language warm, specific, and non-clinical.
- Do not expose private raw chat logs unless the user explicitly asks.

## Rendering

Use the bundled script:

```powershell
python wangpu/content-builder-agent/skills/growth-trajectory-report/scripts/render_growth_report.py --data /reports/growth-report/report-data.json --output-dir /reports/growth-report --pdf --embed-images
```

Flags:

- `--pdf`: also render the report to PDF via local Chrome/Edge.
- `--embed-images`: when producing the PDF, embed images as `data:` URIs so Chrome headless can resolve them without `file://` access. The HTML produced without this flag uses `file://` URIs (better for double-clicking locally).

The script writes:

```text
/reports/growth-report/index.html
/reports/growth-report/<slug>.pdf
```

Do not write new roadshow/demo report artifacts under `roadshow-final-products/`; that directory is legacy reference material.

If PDF rendering fails, keep the HTML and data file and state that the PDF is missing.

## Completion Checklist

- Report data is traceable to source history, script, or artifacts.
- `methodology` explains how every radar dimension was scored and what evidence was used.
- Each `curiosity_themes[*]` entry has an `evidence_image` and a one-sentence `evidence_note`.
- Each `works[*]` entry has a `cover_image`; links to the original artifact (`book.html` / `index.html`) are filled when available.
- `exploration_photos` contains at least 6 entries drawn from `history/<date>/uploads/images/*`.
- `artifact_gallery` contains at least 3 entries covering both storybook and game artifacts.
- `highlights` contains a balanced timeline (童言 / 创作 / 迁移 / 里程碑).
- Visual output is suitable for parent-facing review and roadshow projection.
- Profile updates are evidence-based and written to both JSON and Markdown.
- Events are appended to `/memory/events.jsonl`.
- HTML exists; PDF exists unless Chrome/Edge rendering is unavailable.
