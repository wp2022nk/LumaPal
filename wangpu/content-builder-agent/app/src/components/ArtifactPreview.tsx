import { lazy, Suspense, useEffect, useState } from "react";
import { Download, X } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { AgentRuntime, ArtifactEntry } from "../runtime/types";

const PdfPreview = lazy(() => import("./PdfPreview").then((module) => ({ default: module.PdfPreview })));

export function ArtifactPreview({
  artifact,
  runtime,
  onClose,
}: {
  artifact: ArtifactEntry;
  runtime: AgentRuntime;
  onClose: () => void;
}) {
  const url = runtime.absoluteUrl(artifact.preview_url);
  const [text, setText] = useState("");

  useEffect(() => {
    if (artifact.kind !== "text") {
      return;
    }
    void fetch(url).then((response) => response.text()).then(setText);
  }, [artifact.kind, url]);

  return (
    <div className="preview-overlay" role="dialog" aria-label={`预览 ${artifact.name}`}>
      <header>
        <strong>{artifact.name}</strong>
        <div className="row">
          <a className="icon-button" href={url} download={artifact.name} title="下载">
            <Download size={18} />
          </a>
          <button className="icon-button" onClick={onClose} title="关闭"><X size={20} /></button>
        </div>
      </header>
      <main>
        {artifact.kind === "image" && <img className="full-image" src={url} alt={artifact.name} />}
        {artifact.kind === "pdf" && <Suspense fallback={<p className="muted">正在加载 PDF...</p>}><PdfPreview url={url} /></Suspense>}
        {artifact.kind === "html" && (
          <iframe className="html-preview" src={url} sandbox="allow-scripts" title={artifact.name} />
        )}
        {artifact.kind === "text" && artifact.name.toLowerCase().endsWith(".md") && (
          <article className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></article>
        )}
        {artifact.kind === "text" && !artifact.name.toLowerCase().endsWith(".md") && (
          <pre className="code-preview"><code>{text}</code></pre>
        )}
        {artifact.kind === "download" && (
          <a className="primary-button" href={url} download={artifact.name}>下载文件</a>
        )}
      </main>
    </div>
  );
}
