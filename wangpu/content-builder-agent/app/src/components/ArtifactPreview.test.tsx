import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AgentRuntime, ArtifactEntry } from "../runtime/types";
import { ArtifactPreview } from "./ArtifactPreview";

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
});
