---
name: imagegen
description: Generate polished raster illustrations with the Qwen-backed generate_image tool. Use for any request that needs created visual assets, including story scenes, covers, posters, concepts, or visual variants.
---

# Image Generation

Use `generate_image` for each distinct final image. The tool accepts a detailed prompt, an output PNG path under `/output/`, and an optional supported size.

## Workflow

1. Identify the visual asset's purpose and target output path.
2. Turn the request into a structured prompt using only details that improve the requested result.
3. Generate one image per distinct scene or asset. Do not request many different scenes as variants of one prompt.
4. Inspect the tool result. If it reports failure, do not claim the PNG exists.
5. For a revision, regenerate only the affected image and repeat the constraints that must remain unchanged.

## Prompt Shape

Use these labeled sections when the asset is complex:

```text
Use case: <illustration-story / cover / concept / educational visual / other>
Asset type: <where this image will be used>
Scene/backdrop: <place, time, atmosphere>
Subject: <main subjects, action, expression>
Style/medium: <visual style and rendering medium>
Composition/framing: <camera distance, viewpoint, focal arrangement>
Lighting/mood: <light and emotional tone>
Color palette: <dominant colors>
Continuity anchors: <details that must remain the same across related images>
Constraints: <must include and must preserve>
Avoid: text, watermark, logo, unintended characters, inconsistent redesign
```

Keep prompts concrete and visual. Do not add characters, objects, branding, or story events not supported by the request.

## Related Image Series

For pages, scenes, or assets that must appear as one coherent series:

- Establish a short visual bible before generating images.
- Repeat the full continuity anchors in every page prompt: character species/age, face and hair, clothing and accessories, proportions, key props, rendering medium, palette, and environment language.
- Change only the action, emotion, shot, or location required by the current scene.
- Explicitly prohibit redesign of recurring characters and unexplained changes to clothing, colors, and major props.
- A reference image is optional; do not make creating one a prerequisite for direct scene generation.

## Tool Use

```text
generate_image(
  prompt="<structured prompt>",
  output_path="/output/<artifact-folder>/<image-name>.png",
  size="1024*1024"
)
```

- Use `1024*1024` for square illustrations by default.
- Use only PNG paths below `/output/`.
- If the tool returns an error, keep any accompanying `*-error.txt` file in the artifact manifest.
