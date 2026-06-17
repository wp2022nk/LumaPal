import { useEffect, useRef, useState } from "react";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import type { PDFDocumentProxy } from "pdfjs-dist";

GlobalWorkerOptions.workerSrc = workerUrl;

export function PdfPreview({ url }: { url: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [page, setPage] = useState(1);
  const [pageCount, setPageCount] = useState(1);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    let task: ReturnType<typeof getDocument> | undefined;
    let loadedPdf: PDFDocumentProxy | undefined;
    setError("");
    setPdf(null);
    setPage(1);
    setPageCount(1);
    void fetch(url)
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`${response.status} ${response.statusText}`);
        }
        return response.arrayBuffer();
      })
      .then((buffer) => {
        if (cancelled) {
          return null;
        }
        task = getDocument({ data: new Uint8Array(buffer) });
        return task.promise;
      })
      .then((document) => {
        if (!document) {
          return;
        }
        loadedPdf = document;
        if (cancelled) {
          void document.destroy();
          return;
        }
        setPageCount(document.numPages);
        setPdf(document);
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(String(reason));
        }
      });
    return () => {
      cancelled = true;
      void task?.destroy();
      void loadedPdf?.destroy();
    };
  }, [url]);

  useEffect(() => {
    if (!pdf) {
      return;
    }
    if (page > pdf.numPages) {
      setPage(pdf.numPages);
      return;
    }

    let cancelled = false;
    let renderTask: { cancel: () => void; promise: Promise<unknown> } | undefined;
    void pdf.getPage(page)
      .then(async (pdfPage) => {
        if (cancelled) {
          return;
        }
        const canvas = canvasRef.current;
        const context = canvas?.getContext("2d");
        if (!canvas || !context) {
          return;
        }
        const viewport = pdfPage.getViewport({ scale: 1.5 });
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        context.clearRect(0, 0, canvas.width, canvas.height);
        renderTask = pdfPage.render({ canvasContext: context, viewport });
        await renderTask.promise;
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(String(reason));
        }
      });

    return () => {
      cancelled = true;
      renderTask?.cancel();
    };
  }, [page, pdf]);

  if (error) {
    return <p className="muted">PDF 加载失败：{error}</p>;
  }

  return (
    <div className="pdf-preview">
      <canvas ref={canvasRef} />
      <div className="pdf-controls">
        <button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
        <span>{page} / {pageCount}</span>
        <button disabled={page >= pageCount} onClick={() => setPage((value) => value + 1)}>下一页</button>
      </div>
    </div>
  );
}
