import { describe, expect, it } from "vitest";
import { messageImages, messageReasoning, messageText } from "./messages";

describe("message helpers", () => {
  it("keeps text, reasoning, and image attachments separate", () => {
    const message = {
      content: [
        { type: "thinking", text: "inspect" },
        { type: "image_url", image_url: { url: "data:image/png;base64,AA==" } },
        { type: "text", text: "done" },
      ],
    };
    expect(messageText(message)).toBe("done");
    expect(messageReasoning(message)).toBe("inspect");
    expect(messageImages(message)).toEqual(["data:image/png;base64,AA=="]);
  });
});
