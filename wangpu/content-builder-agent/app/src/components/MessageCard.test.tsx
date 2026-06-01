import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MessageCard } from "./MessageCard";

describe("MessageCard", () => {
  it("keeps tool results collapsed until the user opens them", () => {
    render(
      <MessageCard
        message={{ type: "tool", name: "generate_image", content: "created cover.png" }}
        onImageOpen={() => undefined}
        subagents={[]}
      />,
    );

    expect(screen.getByText("工具结果：generate_image")).toBeInTheDocument();
    expect(screen.getByText("created cover.png").closest("details")).not.toHaveAttribute("open");
  });
});
