import { useEffect, useRef, useState } from "react";
import { GlobalWorkerOptions, getDocument } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

GlobalWorkerOptions.workerSrc = workerUrl;

export function PdfPreview({ url }: { url: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [page, setPage] = useState(1);
  const [pageCount, setPageCount] = useState(1);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    let task: ReturnType<typeof getDocument> | undefined;
    setError("");
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
      .then(async (document) => {
        if (cancelled || !document) {
          return;
        }
        setPageCount(document.numPages);
        const pdfPage = await document.getPage(page);
        const canvas = canvasRef.current;
        if (!canvas) {
          return;
        }
        const viewport = pdfPage.getViewport({ scale: 1.5 });
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        await pdfPage.render({ canvasContext: canvas.getContext("2d")!, viewport }).promise;
      })
      .catch((reason: unknown) => setError(String(reason)));
    return () => {
      cancelled = true;
      void task?.destroy();
    };
  }, [page, url]);

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
