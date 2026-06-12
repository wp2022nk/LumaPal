import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AgentRuntime, ArtifactEntry } from "../runtime/types";
import { ArtifactPreview, rewriteHtmlPreviewAssets } from "./ArtifactPreview";

describe("ArtifactPreview", () => {
  it("opens a thread-scoped image preview at full-screen size", () => {
    const artifact: ArtifactEntry = {
      name: "cover.png",
      path: "artifacts/cover.png",
      kind: "image",
      mime_type: "image/png",
      modified_at: 1,
      preview_url: "/api/content-builder/preview/thread/token/artifacts/cover.png",
      size: 10,
    };
    const runtime = { absoluteUrl: (path: string) => `http://server${path}` } as AgentRuntime;
    render(<ArtifactPreview artifact={artifact} runtime={runtime} onClose={() => undefined} />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("img")).toHaveAttribute("src", `http://server${artifact.preview_url}`);
  });

  it("rewrites local file image paths inside historical HTML previews", () => {
    const runtime = { absoluteUrl: (path: string) => `http://server${path}` } as AgentRuntime;
    const html = rewriteHtmlPreviewAssets(
      '<html><head></head><body><img src="file:///D:/WorkSpace/VScodeProject/2026_AIGC/output/roadshow-final-products/storybook/images/page.png"><img src="file:///D:/WorkSpace/VScodeProject/2026_AIGC/roadshow-final-products/storybook/images/cover.png"></body></html>',
      "/api/content-builder/history-preview/token/roadshow-final-products/storybook/book.html",
      "http://server/api/content-builder/history-preview/token/roadshow-final-products/storybook/book.html",
      runtime,
    );

    expect(html).toContain('<base href="http://server/api/content-builder/history-preview/token/roadshow-final-products/storybook/">');
    expect(html).toContain(
      "http://server/api/content-builder/history-preview/token/roadshow-final-products/storybook/images/page.png",
    );
    expect(html).toContain(
      "http://server/api/content-builder/history-preview/token/roadshow-final-products/storybook/images/cover.png",
    );
  });
});
