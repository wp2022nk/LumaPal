# Growth Report Design Reference

Use this reference for parent-facing child growth trajectory reports in this project.

## Layout Pattern

Use a polished dashboard-document hybrid:

- Hero header: child report title, period, one-sentence growth summary, mood chips.
- Metric strip: conversations, storybooks, exploration captures, parent-child co-creation.
- Ability radar: language expression, scientific thinking, creative imagination, observation, social-emotional growth, with a current/change/trend table.
- Methodology strip: explain how radar scores were inferred from concrete evidence rather than presenting them as mysterious model judgments.
- Curiosity themes: ranked topics with counts, insight, and an evidence photo.
- Works gallery: generated storybooks, games, drawings, and memorable outputs, each with a cover.
- Artifacts corridor: 4-column cover wall of the latest generated artifacts (storybooks, games, story artifacts); each card links to the original HTML.
- Exploration album: 1:1 thumbnails of `history/<date>/uploads/images/*`, grouped by theme; caption + date on each card.
- Highlights timeline: left/right alternating entries with type badge, date, source, and 童言 / 创作 / 迁移 / 里程碑 content.
- Milestone card: narrative explanation of the most important growth shift.
- Suggestions: one deepening activity, one expansion resource, one parent-child interaction suggestion, tagged as 实验 / 阅读 / 互动 chips.
- Next theme preview: one concrete activity prompt for the coming week with numbered starter questions.
- Profile update summary: bullet list of durable observations.

## Photo & Artifact Inclusion

Photos and artifact covers are first-class citizens of the report. They are the bridge between a metric and the lived experience of the child.

- Every `curiosity_themes[*]` entry should carry an `evidence_image` and a one-sentence `evidence_note`. When a theme has no obvious photo, reuse a related `exploration_photos` entry rather than showing a placeholder.
- Every `works[*]` entry should carry a `cover_image` and a `link` to the underlying HTML/PDF artifact. If the cover is unavailable, fall back to a colored placeholder card so the grid stays balanced.
- The artifacts corridor is built from `artifact_gallery`. Keep it curated (3-6 entries) and prefer storybook covers, game screenshots, and story-artifact covers over generic photos.
- The exploration album is a grid of square thumbnails (`aspect-ratio: 1`); each card shows the theme chip, caption, and date. Drop silently when an image is missing.
- For PDF rendering, request the renderer to embed images as `data:` URIs (the `--embed-images` flag) so headless Chrome can resolve them without `file://` access.

## External Design Notes

These notes summarize current parent-facing learning journey and child progress report references:

- TrajectoryMap emphasizes short parent input, a clear visual report, five key areas, color-coded charts, and practical next steps: https://mytrajectorymap.com/
- Brightwheel frames preschool progress reports around social-emotional, language, cognitive, and physical development, plus family support: https://mybrightwheel.com/preschool-progress-report/
- EarlyWorks describes learning journeys as evidence that grows in sophistication over time and connects documentation to planning: https://getearlyworks.com.au/ongoing-assessment/
- Storypark highlights goals, parent aspirations, linked documentation, and family discussion as part of meaningful learning journeys: https://main.storypark.com/feature/documentation
- Parent App's development portfolio language stresses shareable, searchable, up-to-date records of the child's journey: https://www.parent.app/child-development-progress
- Dashboard critique patterns from parent-facing analytics discussions caution against overloading parents with too many tables or small text; keep hierarchy obvious and recommendations concrete.

Applied project interpretation:

- Put "what changed this month" before detailed evidence.
- Keep charts limited to one radar and one ranked theme section.
- Give every metric a plain-language meaning, not just a number.
- Make scoring transparent: question frequency supports curiosity themes; causal words support language expression; cross-scene transfer supports scientific thinking; role-play branches support creative imagination; helping choices support social-emotional growth.
- End with next-week actions that can be tried at home.

## Writing Rules

- Write for parents, not evaluators.
- Prefer "孩子正在..." and "建议..." over diagnostic labels.
- Pair every conclusion with a visible evidence snippet.
- Never imply a clinical assessment. Present scores as learning signals observed in this product's interaction history.
- Preserve warm child language when it is safe and relevant.
- Avoid medical, clinical, or deterministic claims.

## Visual Rules

- Use 2-3 main colors plus neutrals; avoid single-hue monotony.
- Keep the report scannable on mobile and presentable on a TV.
- Use cards for repeated report items only; keep sections as full-width bands.
- Use SVG for charts so the report remains self-contained.
- Use readable Chinese fonts: Microsoft YaHei, Noto Sans SC, Source Han Sans SC, Arial.

## Memory Update Rules

Update the long-term profile only when evidence is durable:

- Stable interest: repeated questions, repeated artifact choices, or explicit preference.
- Storybook style preference: multiple requests or clear engagement with a style.
- Game type preference: completed/chosen game mode or repeated positive reaction.
- Personality signal: observed across multiple interactions or strongly evidenced by parent notes.

Each update should carry source, date, confidence, and a compact evidence summary.
