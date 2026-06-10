---
name: storybook
description: Create an illustrated children's storybook from a story idea or pasted/current conversation, including page planning, consistent Qwen illustrations, HTML layout, and a final PDF. Use when the user asks for a 绘本, storybook, picture book, illustrated children's story, or wants a chat turned into a book.
---

# Illustrated Storybook

Create a complete illustrated picture book for young children from text input. Read `/wangpu/content-builder-agent/skills/imagegen/SKILL.md` before generating illustrations.

## Supported Inputs

- **Story seed:** a theme, character, question, family goal, or a short premise.
- **Conversation source:** relevant messages already present in the current thread or chat text pasted by the user.

For a conversation source, preserve the child's notable choices, invented names, preferred characters, memorable wording, and ending preference. Save a concise provenance summary; do not expose unrelated private conversation details.

## Default Decisions

- Audience: ages 3-7 unless the user specifies otherwise.
- Length: choose the page count that serves the story, normally 8-16 total pages including cover and closing page.
- Language: match the user's language.
- Tone: warm, imaginative, emotionally safe, and suitable for shared family reading.
- Content safety: avoid frightening graphic detail, adult themes, humiliation, or preachy moralizing.

## Required Output

Create one stable directory using a lowercase ASCII hyphenated slug:

```text
/output/storybooks/<slug>/
  source.md
  story.md
  visual-bible.md
  book.json
  images/
    page-00-cover.png
    page-01.png
    ...
  audio/
    page-00-cover.wav
    page-01.wav
    ...
  book.html
  <slug>.pdf
```

- `source.md`: input seed or a concise extraction from the source conversation.
- `story.md`: title, theme, audience, and the page-by-page reading text.
- `visual-bible.md`: recurring character, costume, prop, setting, palette, style, continuity, and avoid rules.
- `book.json`: rendering manifest and illustration source of truth.
- `audio/page-*.wav`: per-page TTS narration (Qwen DashScope); file name matches `book.json` `pages[].id`. Missing files fall back to the browser's `speechSynthesis` automatically.
- `book.html` and `<slug>.pdf`: rendered outputs created by the fixed renderer.

## Workflow

1. Determine whether the source is a new premise or conversation-derived material.
2. Write `source.md`; for pasted/current chat, summarize only the content that belongs in the story.
3. Plan the story arc and pages. Use short read-aloud text and a visual beat on every page.
4. Write `story.md` and `visual-bible.md`.
5. Write `book.json` using the schema below before generating images.
6. For each page, combine its `illustration_prompt` with the complete visual bible constraints and call `generate_image` for its declared `image_path`.
7. Render deterministic HTML and PDF using the bundled renderer after every required image succeeds.
8. Review the artifact set, readability, child appropriateness, and continuity before finishing.

## Book Manifest

Write valid UTF-8 JSON:

```json
{
  "title": "绘本标题",
  "slug": "story-slug",
  "audience": "3-7岁",
  "theme": "核心主题",
  "format": { "page_size": "square-210mm", "page_count": 12 },
  "visual_bible": "与 visual-bible.md 一致的共享视觉约束",
  "pages": [
    {
      "id": "page-00-cover",
      "type": "cover",
      "text": "可选副标题",
      "illustration_prompt": "当前页的具体画面和情绪",
      "image_path": "images/page-00-cover.png",
      "alt_text": "可读插图说明",
      "layout": "full-bleed-title"
    }
  ]
}
```

Allowed layouts:

- `full-bleed-title`: cover or concluding title page.
- `image-top-text-bottom`: default interior reading page.
- `full-bleed-caption`: dramatic or quiet interior page with a readable caption panel.

Every `image_path` must be relative to the storybook directory and target a PNG in `images/`.

## Illustration Prompting

For every page, call:

```text
generate_image(
  prompt="<visual_bible repeated verbatim enough to lock recurring appearance>\n<page scene>\nNo text, no watermark, no logo, no character redesign.",
  output_path="/output/storybooks/<slug>/images/<page-id>.png",
  size="1024*1024"
)
```

- Directly generate each page image; do not require a separate character-sheet image.
- Ensure recurring character anchors appear in every applicable prompt.
- If only one page is weak, revise that page alone while restating all continuity anchors.
- Do not proceed to PDF rendering if a required illustration failed.

## Rendering To HTML, Audio, and PDF

The renderer converts the structured manifest to a Web Audio Storybook HTML (single-page center stage, click-to-read, no buttons), invokes local Chrome Headless to print the PDF, and synthesises one WAV per page using the bundled TTS tool (`content_builder.tools.tts._synthesize_wav`) when `--audio` is on (default).

The agent filesystem uses virtual `/output/...` paths. The local execute tool starts commands from the workspace root and does not translate an executable script path, so invoke the script by workspace-relative path while passing virtual artifact paths:

```powershell
python wangpu/content-builder-agent/skills/storybook/scripts/render_storybook.py --book /output/storybooks/<slug>/book.json --output-dir /output/storybooks/<slug>
```

Flags:

- `--audio` (default on) / `--no-audio`: synthesise per-page WAV using Qwen DashScope. Failures are isolated to a `*-error.txt` per page; the HTML and PDF still render.
- `--no-pdf`: skip the PDF print step (useful for rapid HTML iteration).

The script resolves `/output/...` to the workspace output directory and writes:

```text
/output/storybooks/<slug>/book.html       # Web Audio Storybook
/output/storybooks/<slug>/audio/page-*.wav
/output/storybooks/<slug>/<slug>.pdf
```

## Web Audio Storybook

`book.html` is a single-page center stage with these properties:

- One page is shown at a time; navigation is **click-only** with no on-screen buttons.
- The first click on the stage plays the current page WAV, then auto-advances to the next page with a fade transition.
- The right 25% of the stage goes forward; the left 25% goes back; the middle replays the current page.
- Keyboard: `←` / `→` to navigate, `Space` to replay the current page, `Esc` to stop.
- Missing WAV (or browser audio failure) automatically falls back to `window.speechSynthesis` using the page's `data-text`; behaviour is otherwise identical.
- A small `♪ 点击翻页朗读` hint and a thin dot row mark progress; neither is a clickable button.

If a parent's main concern is the printable spread, render the `<slug>.pdf` (same renderer) for A4-spread printing instead of the Web Audio Storybook.

## Completion Checklist

- Source or conversation extraction is captured without irrelevant private detail.
- `book.json` page count matches its pages and each declared illustration exists.
- Page text is short, speakable, and age-appropriate.
- All repeated characters and setting elements follow `visual-bible.md`.
- `book.html` is the Web Audio Storybook (no on-screen buttons) and the PDF were produced by the bundled renderer.
- `audio/page-*.wav` count matches `pages.length`; any failure is documented in `audio/<page-id>-error.txt` and the HTML still works through the `speechSynthesis` fallback.
- Final response lists every core artifact path and any failed/revised page.
