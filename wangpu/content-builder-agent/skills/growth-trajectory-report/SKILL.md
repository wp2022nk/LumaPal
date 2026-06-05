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
- `/project/../history/YYYY-MM-DD/history.json`
- `/output/` and `/games/` artifacts for the active thread
- User-provided notes, scripts, storybooks, games, drawings, voice transcripts, or reports

If the report is for a roadshow/demo and the user provided a script, use the script as the canonical data source.

## Report Workflow

1. Collect evidence: questions, creative choices, game behavior, storybook outputs, parent-child interactions, emotion snippets, and produced artifacts.
2. Separate stable patterns from one-off observations. Avoid permanent profile updates from weak evidence.
3. Produce `report-data.json` with:
   - overview metrics
   - ability dimensions with current/previous/trend
   - top curiosity themes
   - favorite question type
   - creative works
   - milestones
   - expression progress
   - personalized suggestions
   - next theme
   - profile updates and evidence
4. Render with `scripts/render_growth_report.py`.
5. Update `/memory/profile.json`, regenerate `/memory/profile.md`, and append `/memory/events.jsonl` when the report yields durable observations.

## Visual Standard

Read `references/report-design.md` before designing or revising report layouts.

Reports should be beautiful but immediately understandable:

- Start with a clear monthly/weekly headline and 3-5 key metrics.
- Use grouped cards, a radar chart, trend markers, works gallery, milestone narrative, and next-action suggestions.
- Include evidence snippets so parents see why the report reached its conclusions.
- Keep language warm, specific, and non-clinical.
- Do not expose private raw chat logs unless the user explicitly asks.

## Rendering

Use the bundled script:

```powershell
python /project/skills/growth-trajectory-report/scripts/render_growth_report.py --data /output/growth-report/report-data.json --output-dir /output/growth-report --pdf
```

The script writes:

```text
/output/growth-report/index.html
/output/growth-report/<slug>.pdf
```

If PDF rendering fails, keep the HTML and data file and state that the PDF is missing.

## Completion Checklist

- Report data is traceable to source history, script, or artifacts.
- Visual output is suitable for parent-facing review and roadshow projection.
- Profile updates are evidence-based and written to both JSON and Markdown.
- Events are appended to `/memory/events.jsonl`.
- HTML exists; PDF exists unless Chrome/Edge rendering is unavailable.
