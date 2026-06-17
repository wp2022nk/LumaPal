import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getDocument } from "pdfjs-dist";
import { PdfPreview } from "./PdfPreview";

vi.mock("pdfjs-dist", () => ({
  GlobalWorkerOptions: {},
  getDocument: vi.fn(),
}));

vi.mock("pdfjs-dist/build/pdf.worker.min.mjs?url", () => ({
  default: "pdf-worker.js",
}));

describe("PdfPreview", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(new ArrayBuffer(8), { status: 200 }));
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      clearRect: vi.fn(),
    })) as unknown as HTMLCanvasElement["getContext"];
  });

  it("loads the PDF once and renders page changes from the cached document", async () => {
    const renderedPages: number[] = [];
    const pdf = {
      destroy: vi.fn(),
      getPage: vi.fn(async (page: number) => ({
        getViewport: () => ({ width: 120, height: 120 }),
        render: () => {
          renderedPages.push(page);
          return { cancel: vi.fn(), promise: Promise.resolve() };
        },
      })),
      numPages: 2,
    };
    vi.mocked(getDocument).mockReturnValue({
      destroy: vi.fn(),
      promise: Promise.resolve(pdf),
    } as unknown as ReturnType<typeof getDocument>);

    render(<PdfPreview url="http://server/book.pdf" />);

    expect(await screen.findByText("1 / 2")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button")[1]);

    await waitFor(() => expect(renderedPages).toContain(2));
    expect(getDocument).toHaveBeenCalledTimes(1);
    expect(pdf.getPage).toHaveBeenCalledWith(1);
    expect(pdf.getPage).toHaveBeenCalledWith(2);
  });
});
