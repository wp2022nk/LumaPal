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
  const displayName = artifactPreviewTitle(artifact);
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
    <div className="preview-overlay" role="dialog" aria-label={`预览 ${displayName}`}>
      <header>
        <strong>{displayName}</strong>
        <div className="row">
          <a className="icon-button" href={url} download={artifact.name} title="下载">
            <Download size={18} />
          </a>
          <button className="icon-button" onClick={onClose} title="关闭"><X size={20} /></button>
        </div>
      </header>
      <main>
        {artifact.kind === "image" && <img className="full-image" src={url} alt={displayName} />}
        {artifact.kind === "pdf" && <Suspense fallback={<p className="muted">正在加载 PDF...</p>}><PdfPreview url={url} /></Suspense>}
        {artifact.kind === "html" && (
          <iframe className="html-preview" src={htmlUrl || url} sandbox="allow-scripts" title={displayName} />
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

function artifactPreviewTitle(artifact: ArtifactEntry): string {
  const candidate = artifact.title || artifact.name;
  if (artifact.kind === "image" && !/[\u4e00-\u9fff]/.test(candidate)) {
    return "图片预览";
  }
  return candidate;
}

export function rewriteHtmlPreviewAssets(
  html: string,
  previewPath: string,
  previewUrl: string,
  runtime: AgentRuntime,
): string {
  const baseUrl = new URL("./", previewUrl).toString();
  const rewriteLocalPath = (value: string): string => {
    return resolvePreviewAssetUrl(value, previewPath, previewUrl, runtime);
  };
  const rewriteSrcset = (value: string): string => {
    return value
      .split(",")
      .map((candidate) => {
        const trimmed = candidate.trim();
        if (!trimmed) {
          return candidate;
        }
        const [src, ...descriptor] = trimmed.split(/\s+/);
        return [rewriteLocalPath(src), ...descriptor].join(" ");
      })
      .join(", ");
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
      /\bsrcset=("|')([^"']+)\1/gi,
      (_match, quote: string, value: string) => `srcset=${quote}${rewriteSrcset(value)}${quote}`,
    )
    .replace(
      /url\((["']?)([^"')]+)\1\)/gi,
      (_match, quote: string, value: string) => `url(${quote}${rewriteLocalPath(value.trim())}${quote})`,
    );
}

export function resolvePreviewAssetUrl(
  value: string,
  previewPath: string,
  previewUrl: string,
  runtime: AgentRuntime,
): string {
  if (!value || value.startsWith("#") || /^(data|blob|https?):/i.test(value)) {
    return value;
  }
  if (value.startsWith("//")) {
    return `${new URL(previewUrl).protocol}${value}`;
  }

  const context = previewContext(previewPath);
  if (!context) {
    return new URL(value, new URL("./", previewUrl)).toString();
  }

  const baseUrl = new URL("./", previewUrl);
  const decoded = decodePreviewPath(value);
  const fromLocalPath = virtualPathFromLocalPath(decoded, context);
  if (fromLocalPath) {
    return runtime.absoluteUrl(`${context.routePrefix}${fromLocalPath}`);
  }

  if (value.startsWith("/")) {
    const rootVirtualPath = virtualPathFromRootPath(value, context);
    if (rootVirtualPath) {
      return runtime.absoluteUrl(`${context.routePrefix}${rootVirtualPath}`);
    }
    return new URL(value.slice(1), runtime.absoluteUrl("/")).toString();
  }

  return new URL(value, baseUrl).toString();
}

type PreviewMode = "thread" | "history";

interface PreviewContext {
  mode: PreviewMode;
  routePrefix: string;
  virtualPath: string;
}

function previewContext(previewPath: string): PreviewContext | null {
  const history = previewPath.match(/^(\/api\/content-builder\/history-preview\/[^/]+\/)(.+)$/);
  if (history) {
    return { mode: "history", routePrefix: history[1], virtualPath: history[2] };
  }
  const thread = previewPath.match(/^(\/api\/content-builder\/preview\/[^/]+\/[^/]+\/)(.+)$/);
  if (thread) {
    return { mode: "thread", routePrefix: thread[1], virtualPath: thread[2] };
  }
  return null;
}

function decodePreviewPath(value: string): string {
  try {
    return decodeURI(value).replace(/^file:\/\/\/?/i, "").replace(/\\/g, "/");
  } catch {
    return value.replace(/^file:\/\/\/?/i, "").replace(/\\/g, "/");
  }
}

function virtualPathFromLocalPath(path: string, context: PreviewContext): string {
  const archiveMatch = path.match(/\/history\/\d{4}-\d{2}-\d{2}\/conversations\/[^/]+\/(.+)$/);
  if (archiveMatch) {
    return context.mode === "history"
      ? `history/${path.slice(path.indexOf("/history/") + "/history/".length)}`
      : archiveMatch[1];
  }

  const outputIndex = path.indexOf("/output/");
  if (outputIndex >= 0) {
    const outputPath = path.slice(outputIndex + "/output/".length);
    if (context.mode === "history" && outputPath.startsWith("roadshow-final-products/")) {
      return outputPath;
    }
    return context.mode === "thread" ? threadPathFromOutputPath(outputPath, context) : `output/${outputPath}`;
  }

  const roadshowIndex = path.indexOf("/roadshow-final-products/");
  if (roadshowIndex >= 0) {
    const roadshowPath = path.slice(roadshowIndex + "/roadshow-final-products/".length);
    return context.mode === "history" ? `roadshow-final-products/${roadshowPath}` : roadshowPath;
  }

  const threadPath = knownThreadPath(path);
  if (threadPath) {
    return threadPath;
  }

  const assetFolder = path.match(/\/(audio|images|assets|media|static)\/(.+)$/);
  if (assetFolder) {
    return `${parentVirtualDirectory(context.virtualPath)}/${assetFolder[1]}/${assetFolder[2]}`;
  }

  return "";
}

function virtualPathFromRootPath(value: string, context: PreviewContext): string {
  const normalized = value.replace(/^\/+/, "");
  if (context.mode === "history" && /^(history|output|roadshow-final-products)\//.test(normalized)) {
    return normalized;
  }
  if (context.mode === "thread" && /^(artifacts|uploads|workspace)\//.test(normalized)) {
    return normalized;
  }
  if (normalized.startsWith("output/")) {
    return context.mode === "thread"
      ? threadPathFromOutputPath(normalized.slice("output/".length), context)
      : normalized;
  }
  if (/^(storybooks|games|reports|growth-report)\//.test(normalized)) {
    return context.mode === "thread"
      ? threadPathFromOutputPath(normalized, context)
      : `${archiveRoot(context.virtualPath)}/${threadPathFromOutputPath(normalized, context)}`;
  }
  if (normalized.startsWith("uploads/")) {
    return context.mode === "thread" ? normalized : `${archiveRoot(context.virtualPath)}/${normalized}`;
  }
  return "";
}

function threadPathFromOutputPath(path: string, context: PreviewContext): string {
  const normalized = path.replace(/^\/+/, "");
  if (normalized.startsWith("storybooks/")) {
    return `artifacts/${normalized}`;
  }
  if (normalized.startsWith("games/")) {
    return `artifacts/${normalized}`;
  }
  if (normalized.startsWith("reports/")) {
    return `artifacts/${normalized}`;
  }
  if (normalized.startsWith("growth-report/")) {
    return `artifacts/reports/${normalized}`;
  }
  if (normalized.startsWith("uploads/")) {
    return normalized;
  }
  if (/^(artifacts|workspace)\//.test(normalized)) {
    return normalized;
  }
  return `${parentVirtualDirectory(context.virtualPath)}/${normalized}`;
}

function knownThreadPath(path: string): string {
  const match = path.match(/\/((?:artifacts|uploads|workspace)\/.+)$/);
  return match?.[1] || "";
}

function parentVirtualDirectory(path: string): string {
  const clean = path.replace(/^\/+|\/+$/g, "");
  const index = clean.lastIndexOf("/");
  return index >= 0 ? clean.slice(0, index) : "";
}

function archiveRoot(path: string): string {
  const match = path.match(/^(history\/\d{4}-\d{2}-\d{2}\/conversations\/[^/]+)/);
  return match?.[1] || parentVirtualDirectory(path);
}
