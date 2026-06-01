import { describe, expect, it } from "vitest";
import { nextTextDelta } from "./ttsDelta";

describe("nextTextDelta", () => {
  it("deduplicates cumulative and overlapping streamed text for TTS", () => {
    expect(nextTextDelta("你好", "你好世界")).toBe("世界");
    expect(nextTextDelta("内容创作", "创作完成")).toBe("完成");
    expect(nextTextDelta("完成", "完成")).toBe("");
  });
});
