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
  const [htmlUrl, setHtmlUrl] = useState("");

  useEffect(() => {
    if (artifact.kind !== "text") {
      return;
    }
    void fetch(url).then((response) => response.text()).then(setText);
  }, [artifact.kind, url]);

  useEffect(() => {
    if (artifact.kind !== "html") {
      setHtmlUrl("");
      return;
    }
    let active = true;
    let objectUrl = "";
    void fetch(url)
      .then((response) => {
        if (!response.ok) {
          throw new Error(`${response.status} ${response.statusText}`);
        }
        return response.text();
      })
      .then((html) => {
        if (!active) {
          return;
        }
        const rewritten = rewriteHtmlPreviewAssets(html, artifact.preview_url, url, runtime);
        objectUrl = URL.createObjectURL(new Blob([rewritten], { type: "text/html" }));
        setHtmlUrl(objectUrl);
      })
      .catch(() => {
        if (active) {
          setHtmlUrl(url);
        }
      });
    return () => {
      active = false;
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl);
      }
    };
  }, [artifact.kind, artifact.preview_url, runtime, url]);

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
          <iframe className="html-preview" src={htmlUrl || url} sandbox="allow-scripts" title={artifact.name} />
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

export function rewriteHtmlPreviewAssets(
  html: string,
  previewPath: string,
  previewUrl: string,
  runtime: AgentRuntime,
): string {
  const baseUrl = new URL("./", previewUrl).toString();
  const root = runtime.absoluteUrl("/");
  const tokenPrefix = previewPath.match(/^(\/api\/content-builder\/(?:history-preview\/[^/]+|preview\/[^/]+\/[^/]+)\/)/)?.[1] || "";
  const rewriteLocalPath = (value: string): string => {
    if (!value || value.startsWith("#") || /^(data|blob|https?):/i.test(value)) {
      return value;
    }
    const decoded = value.replace(/^file:\/\/\/?/i, "").replace(/\\/g, "/");
    const outputIndex = decoded.indexOf("/output/");
    if (outputIndex >= 0 && tokenPrefix) {
      return runtime.absoluteUrl(`${tokenPrefix}${decoded.slice(outputIndex + "/output/".length)}`);
    }
    const historyIndex = decoded.indexOf("/history/");
    if (historyIndex >= 0 && tokenPrefix) {
      return runtime.absoluteUrl(`${tokenPrefix}${decoded.slice(historyIndex + 1)}`);
    }
    const roadshowIndex = decoded.indexOf("/roadshow-final-products/");
    if (roadshowIndex >= 0 && tokenPrefix) {
      return runtime.absoluteUrl(`${tokenPrefix}${decoded.slice(roadshowIndex + 1)}`);
    }
    if (value.startsWith("/output/") && tokenPrefix) {
      return runtime.absoluteUrl(`${tokenPrefix}${value.slice("/output/".length)}`);
    }
    if (value.startsWith("/history/") && tokenPrefix) {
      return runtime.absoluteUrl(`${tokenPrefix}${value.slice(1)}`);
    }
    if (value.startsWith("/roadshow-final-products/") && tokenPrefix) {
      return runtime.absoluteUrl(`${tokenPrefix}${value.slice(1)}`);
    }
    if (value.startsWith("/")) {
      return new URL(value.slice(1), root).toString();
    }
    return value;
  };
  const withBase = /<base\s/i.test(html)
    ? html
    : html.replace(/<head([^>]*)>/i, `<head$1><base href="${baseUrl}">`);
  return withBase
    .replace(
      /\b(src|href|poster)=("|')([^"']+)\2/gi,
      (_match, attr: string, quote: string, value: string) => `${attr}=${quote}${rewriteLocalPath(value)}${quote}`,
    )
    .replace(
      /url\((["']?)([^"')]+)\1\)/gi,
      (_match, quote: string, value: string) => `url(${quote}${rewriteLocalPath(value.trim())}${quote})`,
    );
}
