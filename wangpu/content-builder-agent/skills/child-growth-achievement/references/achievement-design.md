# Child Growth Achievement Design Reference

Use this reference for child-facing achievement panels generated from growth reports.

## Page Shape

- Hero: large child-facing title, period pill, one encouraging summary, one memorable quote, and a warm generated illustration.
- Achievement cards: five badges, each with a grand title, one short child-readable message, a gentle evidence line, and star marks.
- Badge map: four to six steps showing how the child moved from observation to imagination to finished work.
- Curiosity lab: a generated scene that makes the learning theme tangible.
- Works treasure box: storybooks, games, covers, drawings, or photos that prove the achievement.
- Next challenge: three concrete playful missions for the next theme.

## Tone

- Address the child directly with "你".
- Prefer "你已经会..." and "你把..." over evaluation language.
- Do not use clinical, diagnostic, or school-report language.
- Do not show raw metrics, radar charts, methodology, or adult-facing scoring explanations.

## Badge Titles

Default titles:

- `云朵故事魔法师` for story expression and imagination.
- `自然观察小侦探` for everyday observation.
- `科学问题小队长` for causal reasoning and repeated "why" questions.
- `绘本创作小导演` for storybook, game, image, or multimodal production.
- `温柔合作小伙伴` for parent-child co-creation and helping choices.

Adapt titles only when the source report has a clearly different theme.

## Image Rules

- Generate three raster illustrations with `$imagegen` built-in mode.
- Save final images inside the artifact directory under `images/generated/`.
- Copy the hero image to `cover.png` so the app history shelf has a preview.
- Prompt generated images with "no text, no letters, no numbers, no logos, no watermark".
- Render all Chinese copy in HTML/CSS instead of asking image generation to draw text.

## Visual Rules

- Use a bright mixed palette: sky blue, mint, warm yellow, coral, and small lavender accents.
- Use cards only for repeated achievements and works; keep the page as an open poster, not nested panels.
- Keep border radii playful but controlled.
- Ensure mobile layouts collapse to one column and card text does not overflow.
- In print/PDF mode, avoid shadows that can become muddy and keep each major section together.
