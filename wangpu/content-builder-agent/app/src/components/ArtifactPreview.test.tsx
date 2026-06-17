import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AgentRuntime, ArtifactEntry } from "../runtime/types";
import { ArtifactPreview, resolvePreviewAssetUrl, rewriteHtmlPreviewAssets } from "./ArtifactPreview";

describe("ArtifactPreview", () => {
  it("opens a thread-scoped image preview at full-screen size", () => {
    const artifact: ArtifactEntry = {
      name: "cover.png",
      title: "月亮邮差封面",
      path: "artifacts/cover.png",
      kind: "image",
      mime_type: "image/png",
      modified_at: 1,
      preview_url: "/api/content-builder/preview/thread/token/artifacts/cover.png",
      size: 10,
    };
    const runtime = { absoluteUrl: (path: string) => `http://server${path}` } as AgentRuntime;
    render(<ArtifactPreview artifact={artifact} runtime={runtime} onClose={() => undefined} />);
    expect(screen.getByRole("dialog", { name: "预览 月亮邮差封面" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "月亮邮差封面" })).toHaveAttribute("src", `http://server${artifact.preview_url}`);
  });

  it("uses a friendly title for hash-named image previews", () => {
    const artifact: ArtifactEntry = {
      name: "36a38f54b2514b919.png",
      title: "36a38f54b2514b919",
      path: "uploads/images/36a38f54b2514b919.png",
      kind: "image",
      mime_type: "image/png",
      modified_at: 1,
      preview_url: "/api/content-builder/history-preview/token/uploads/images/36a38f54b2514b919.png",
      size: 10,
    };
    const runtime = { absoluteUrl: (path: string) => `http://server${path}` } as AgentRuntime;
    render(<ArtifactPreview artifact={artifact} runtime={runtime} onClose={() => undefined} />);
    expect(screen.getByRole("dialog", { name: "预览 图片预览" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "图片预览" })).toHaveAttribute("src", `http://server${artifact.preview_url}`);
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

  it("keeps non-roadshow output paths resolvable through the history preview route", () => {
    const runtime = { absoluteUrl: (path: string) => `http://server${path}` } as AgentRuntime;
    const html = rewriteHtmlPreviewAssets(
      '<html><head></head><body><img src="file:///D:/WorkSpace/VScodeProject/2026_AIGC/output/storybooks/little-star/images/page.png"></body></html>',
      "/api/content-builder/history-preview/token/history/2026-06-13/conversations/thread/artifacts/storybooks/little-star/book.html",
      "http://server/api/content-builder/history-preview/token/history/2026-06-13/conversations/thread/artifacts/storybooks/little-star/book.html",
      runtime,
    );

    expect(html).toContain(
      "http://server/api/content-builder/history-preview/token/output/storybooks/little-star/images/page.png",
    );
  });

  it("rewrites relative HTML assets to absolute preview URLs for mobile WebViews", () => {
    const runtime = { absoluteUrl: (path: string) => `http://server${path}` } as AgentRuntime;
    const original = '<html><head></head><body><img src="images/page.png" srcset="images/page-small.png 480w, images/page.png 960w"><video poster="./cover.jpg"></video><style>.hero{background:url(images/bg.png)}</style></body></html>';
    const html = rewriteHtmlPreviewAssets(
      original,
      "/api/content-builder/history-preview/token/history/2026-06-13/conversations/thread/artifacts/storybooks/little-star/book.html",
      "http://server/api/content-builder/history-preview/token/history/2026-06-13/conversations/thread/artifacts/storybooks/little-star/book.html",
      runtime,
    );

    expect(original).toContain('src="images/page.png"');
    expect(html).toContain(
      'src="http://server/api/content-builder/history-preview/token/history/2026-06-13/conversations/thread/artifacts/storybooks/little-star/images/page.png"',
    );
    expect(html).toContain(
      'srcset="http://server/api/content-builder/history-preview/token/history/2026-06-13/conversations/thread/artifacts/storybooks/little-star/images/page-small.png 480w, http://server/api/content-builder/history-preview/token/history/2026-06-13/conversations/thread/artifacts/storybooks/little-star/images/page.png 960w"',
    );
    expect(html).toContain(
      'poster="http://server/api/content-builder/history-preview/token/history/2026-06-13/conversations/thread/artifacts/storybooks/little-star/cover.jpg"',
    );
    expect(html).toContain(
      "url(http://server/api/content-builder/history-preview/token/history/2026-06-13/conversations/thread/artifacts/storybooks/little-star/images/bg.png)",
    );
  });

  it("rewrites legacy file storybook image paths relative to the opened book", () => {
    const runtime = { absoluteUrl: (path: string) => `http://server${path}` } as AgentRuntime;
    const html = rewriteHtmlPreviewAssets(
      '<html><head></head><body><img src="file:///D:/WorkSpace/VScodeProject/2026_AIGC/storybooks/little-companions/images/page-00-cover.png"></body></html>',
      "/api/content-builder/history-preview/token/history/2026-06-13/conversations/thread/artifacts/storybooks/little-companions/book.html",
      "http://server/api/content-builder/history-preview/token/history/2026-06-13/conversations/thread/artifacts/storybooks/little-companions/book.html",
      runtime,
    );

    expect(html).toContain(
      'src="http://server/api/content-builder/history-preview/token/history/2026-06-13/conversations/thread/artifacts/storybooks/little-companions/images/page-00-cover.png"',
    );
  });

  it("rewrites current thread local archive paths to thread preview URLs", () => {
    const runtime = { absoluteUrl: (path: string) => `http://server${path}` } as AgentRuntime;
    const html = rewriteHtmlPreviewAssets(
      '<html><head></head><body><img src="file:///D:/WorkSpace/VScodeProject/2026_AIGC/history/2026-06-13/conversations/thread-a/artifacts/storybooks/moon/images/page.png"><audio src="audio/page-01.wav"></audio></body></html>',
      "/api/content-builder/preview/thread-a/token/artifacts/storybooks/moon/book.html",
      "http://server/api/content-builder/preview/thread-a/token/artifacts/storybooks/moon/book.html",
      runtime,
    );

    expect(html).toContain(
      'src="http://server/api/content-builder/preview/thread-a/token/artifacts/storybooks/moon/images/page.png"',
    );
    expect(html).toContain(
      'src="http://server/api/content-builder/preview/thread-a/token/artifacts/storybooks/moon/audio/page-01.wav"',
    );
  });

  it("maps current thread output-style paths into artifact preview roots", () => {
    const runtime = { absoluteUrl: (path: string) => `http://server${path}` } as AgentRuntime;

    expect(resolvePreviewAssetUrl(
      "/output/storybooks/moon/images/page.png",
      "/api/content-builder/preview/thread-a/token/artifacts/storybooks/moon/book.html",
      "http://server/api/content-builder/preview/thread-a/token/artifacts/storybooks/moon/book.html",
      runtime,
    )).toBe("http://server/api/content-builder/preview/thread-a/token/artifacts/storybooks/moon/images/page.png");
    expect(resolvePreviewAssetUrl(
      "/reports/growth-report/chart.png",
      "/api/content-builder/preview/thread-a/token/artifacts/reports/growth-report/report.html",
      "http://server/api/content-builder/preview/thread-a/token/artifacts/reports/growth-report/report.html",
      runtime,
    )).toBe("http://server/api/content-builder/preview/thread-a/token/artifacts/reports/growth-report/chart.png");
  });
});
