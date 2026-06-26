---
name: child-growth-achievement
description: Generate child-facing growth achievement panels from this project's parent-facing growth reports, child history, artifacts, and photos. Use when asked for 儿童成长成就, 成长成就面板, child growth achievement, kid-facing growth report, badge board, achievement poster, or a playful child version of a growth trajectory report.
---

# Child Growth Achievement

Create a child-facing achievement panel from the same evidence used by the parent-facing growth trajectory report. The output should feel like a celebratory badge poster for the child, not a data dashboard for adults.

## Inputs To Inspect

- Existing parent report data: `/reports/growth-report/report-data.json`, `roadshow-final-products/growth-report/report-data.json`, or a user-provided report JSON.
- Generated artifacts: storybooks, games, drawings, photos, and prior reports under `/output/`, `/reports/`, `history/`, and `roadshow-final-products/`.
- Existing child profile memory when available, but avoid exposing private raw chat logs in the final panel.

## Required Output

Write a directory named `child-growth-achievement` under the active reports folder:

```text
/reports/child-growth-achievement/child-achievement-data.json
/reports/child-growth-achievement/index.html
/reports/child-growth-achievement/<child-name>的成长成就-<period>.pdf
/reports/child-growth-achievement/images/generated/*.png
```

For a roadshow/demo artifact, write the same structure under:

```text
roadshow-final-products/child-growth-achievement/
```

## Workflow

1. Read the source report JSON or source history.
2. Convert adult-facing dimensions into child-facing achievements:
   - language/story expression -> `云朵故事魔法师`
   - observation/exploration -> `自然观察小侦探`
   - science/causal reasoning -> `科学问题小队长`
   - creative outputs -> `绘本创作小导演`
   - collaboration/social emotion -> `温柔合作小伙伴`
3. Build `child-achievement-data.json` with at least:
   - `title`, `period`, `child_name`, `subtitle`, `hero_quote`
   - `achievements`: title, badge, message, evidence, level
   - `badge_map`: four to six child-friendly steps
   - `works`: artifact title, type, highlight, cover image, optional link
   - `challenge_next`: next theme, message, concrete steps
   - `generated_images`: paths for `hero_adventure`, `badge_map`, and `curiosity_lab`
4. Use `$imagegen` built-in mode for three project-bound illustrations:
   - `hero-adventure.png`
   - `badge-map.png`
   - `curiosity-lab.png`
   Prompt all generated images with: bright children's illustration, warm, sticker-like, no text, no letters, no numbers, no logos, no watermark.
5. Copy generated images into `images/generated/` and copy the hero image to `cover.png` for history card previews.
6. Render with:

```powershell
python wangpu/content-builder-agent/skills/child-growth-achievement/scripts/render_child_growth_achievement.py --data /reports/child-growth-achievement/child-achievement-data.json --output-dir /reports/child-growth-achievement --pdf --embed-images
```

Use a direct filesystem path instead of `/reports/...` when rendering a roadshow/demo directory.

## Writing Rules

- Write for the child first. Use encouragement, concrete achievements, and short sentences.
- Do not show radar charts, scoring methodology, data tables, or parent-facing analysis.
- Keep evidence visible but gentle: "你把..." and "你已经会..." are preferred.
- Keep all important Chinese text in HTML/CSS, not inside generated images.
- Use titles like `云朵故事魔法师`, `自然观察小侦探`, `科学问题小队长`, `绘本创作小导演`, and `温柔合作小伙伴`.
- Preserve one memorable child quote as the hero quote when available.

## Visual Standard

Read `references/achievement-design.md` before changing the layout. The panel should feel like a playful achievement poster with badges, stickers, a challenge map, artwork, and a next-level mission.

## Completion Checklist

- `child-achievement-data.json` exists and is traceable to report/history evidence.
- Three generated images are saved in the project, not only under `$CODEX_HOME`.
- `cover.png` exists for history preview cards.
- HTML exists and uses child-facing language.
- PDF exists unless Chrome/Edge headless rendering is unavailable.
- The history artifact path uses `child-growth-achievement` so the app categorizes it as `child_growth_achievement`.
