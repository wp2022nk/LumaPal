import DOMPurify from "dompurify";
import MarkdownIt from "markdown-it";
import type { PendingImage, SchemaField, TodoItem, ToolCallView, ToolEventView, ToolManifest } from "./types";

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

export function readableError(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  try {
    return JSON.stringify(error);
  } catch {
    return "未知错误";
  }
}

export function renderMarkdown(value: string): string {
  return DOMPurify.sanitize(markdown.render(value || ""));
}

export function roleOf(message: any): string {
  if (!message) return "";

  const serializedRole = roleFromSerializedMessage(message);
  if (serializedRole) return serializedRole;

  if (typeof message.role === "string") return message.role;
  if (typeof message.type === "string" && message.type !== "constructor") {
    return message.type;
  }

  if (typeof message._getType === "function") {
    return message._getType();
  }

  if (message.constructor?.name === "HumanMessage") return "human";
  if (message.constructor?.name === "HumanMessageChunk") return "human";
  if (message.constructor?.name === "AIMessage") return "ai";
  if (message.constructor?.name === "AIMessageChunk") return "ai";
  if (message.constructor?.name === "ToolMessage") return "tool";

  if (typeof message.kwargs?.type === "string") return message.kwargs.type;
  if (typeof message.lc_kwargs?.type === "string") return message.lc_kwargs.type;

  return String(message?.type ?? message?.role ?? "");
}

export function roleFromSerializedMessage(message: any): string {
  const typeName = Array.isArray(message?.id)
    ? String(message.id.at(-1) ?? "")
    : "";
  const candidates = [
    message?.kwargs?.type,
    message?.lc_kwargs?.type,
    typeName,
    message?.type === "constructor" ? message?.kwargs?.message_type : null,
  ]
    .filter((value) => typeof value === "string")
    .map((value) => value.toLowerCase());

  for (const candidate of candidates) {
    if (candidate.includes("human")) return "human";
    if (candidate.includes("ai")) return "ai";
    if (candidate.includes("assistant")) return "assistant";
    if (candidate.includes("tool")) return "tool";
    if (candidate === "user") return "user";
  }

  return "";
}

export function messageId(message: any, index = 0): string {
  const id =
    message?.message_id ??
    message?.uuid ??
    message?.lc_kwargs?.id ??
    message?.kwargs?.id ??
    (Array.isArray(message?.id) ? null : message?.id);

  if (id != null && id !== "") return String(id);

  return `${roleOf(message)}-${index}`;
}

export function messageContent(message: any): any {
  return (
    message?.content ??
    message?.kwargs?.content ??
    message?.lc_kwargs?.content ??
    message?.text ??
    ""
  );
}

export function textFromContent(content: unknown): string {
  if (typeof content === "string") return content;

  if (Array.isArray(content)) {
    return content
      .map((block: any) => {
        if (typeof block === "string") return block;
        if (block?.type === "text") return block.text ?? "";
        if (typeof block?.text === "string") return block.text;
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }

  if (content && typeof content === "object") {
    if (typeof (content as any).text === "string") return (content as any).text;
    if (typeof (content as any).content === "string") return (content as any).content;
  }

  return "";
}

export function imageUrlsFromContent(content: unknown): string[] {
  const urls: string[] = [];

  if (Array.isArray(content)) {
    for (const block of content as any[]) {
      if (typeof block?.image_url === "string") urls.push(block.image_url);
      if (typeof block?.image_url?.url === "string") urls.push(block.image_url.url);
      if (typeof block?.url === "string" && block?.type === "image_url") {
        urls.push(block.url);
      }
      if (block?.type === "image") {
        const url = block.source?.url ?? block.url;
        if (typeof url === "string") urls.push(url);
      }
    }
  }

  return Array.from(new Set(urls.filter(Boolean)));
}

export function messageText(message: any): string {
  return textFromContent(messageContent(message));
}

export function messageImages(message: any): string[] {
  return imageUrlsFromContent(messageContent(message));
}

export function parseJsonMaybe(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

export function normalizeObject(value: unknown): Record<string, unknown> {
  const parsed = parseJsonMaybe(value);
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
    return parsed as Record<string, unknown>;
  }
  if (parsed == null || parsed === "") return {};
  return { value: parsed };
}

export function parseToolCalls(message: any): ToolCallView[] {
  const direct = Array.isArray(message?.tool_calls)
    ? message.tool_calls
    : Array.isArray(message?.kwargs?.tool_calls)
      ? message.kwargs.tool_calls
      : [];
  const additionalKwargs =
    message?.additional_kwargs ??
    message?.kwargs?.additional_kwargs ??
    message?.lc_kwargs?.additional_kwargs;
  const openAi = Array.isArray(additionalKwargs?.tool_calls)
    ? additionalKwargs.tool_calls
    : [];
  const calls = direct.length ? direct : openAi;

  return calls.map((call: any, index: number) => {
    const name = call.name ?? call.function?.name ?? `tool_${index + 1}`;
    const args = call.args ?? call.function?.arguments ?? {};
    return {
      id: String(call.id ?? call.call_id ?? `${messageId(message)}:${name}:${index}`),
      name: String(name),
      args: normalizeObject(args),
    };
  });
}

export function schemaFields(tool: ToolManifest | undefined): SchemaField[] {
  const schema = tool?.schema as any;
  const properties =
    schema?.properties && typeof schema.properties === "object"
      ? schema.properties
      : {};
  const required = new Set(Array.isArray(schema?.required) ? schema.required : []);

  return Object.entries(properties).map(([name, spec]: [string, any]) => ({
    name,
    type: String(
      spec?.type ??
        spec?.anyOf
          ?.map((item: any) => item.type)
          .filter(Boolean)
          .join(" | ") ??
        "value",
    ),
    required: required.has(name),
    description: String(spec?.description ?? ""),
  }));
}

export function summarizeResult(content = ""): string {
  const clean = content.replace(/\s+/g, " ").trim();
  if (!clean) return "已完成";
  return clean.length > 180 ? `${clean.slice(0, 180)}...` : clean;
}

export function toolStatus(event: ToolEventView): string {
  if (event.result?.status === "error") return "error";
  return event.running ? "running" : "finished";
}

export function todoLabel(todo: TodoItem): string {
  return String(todo.content ?? todo.title ?? "未命名任务");
}

export function statusText(status?: string): string {
  if (status === "completed" || status === "complete" || status === "finished") {
    return "完成";
  }
  if (status === "in_progress" || status === "running") return "执行中";
  if (status === "error") return "失败";
  return "等待";
}

export function shortId(value: string | null | undefined): string {
  if (!value) return "未创建";
  return value.length > 12 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value;
}

export function pathDepth(path: string): number {
  if (!path || path === ".") return 0;
  return Math.max(0, path.split(/[\\/]/).length - 1);
}

export function humanSize(bytes: number): string {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function shortDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function fileToPendingImage(file: File): Promise<PendingImage> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => {
      resolve({
        id: crypto.randomUUID(),
        name: file.name,
        size: file.size,
        dataUrl: String(reader.result),
      });
    };
    reader.readAsDataURL(file);
  });
}

export function simpleLineDiff(before: string, after: string): Array<{ kind: "same" | "add" | "remove"; text: string }> {
  const oldLines = before.split("\n");
  const newLines = after.split("\n");
  const rows: Array<{ kind: "same" | "add" | "remove"; text: string }> = [];
  const max = Math.max(oldLines.length, newLines.length);
  for (let index = 0; index < max; index += 1) {
    const oldLine = oldLines[index];
    const newLine = newLines[index];
    if (oldLine === newLine) {
      if (oldLine !== undefined) rows.push({ kind: "same", text: oldLine });
    } else {
      if (oldLine !== undefined) rows.push({ kind: "remove", text: oldLine });
      if (newLine !== undefined) rows.push({ kind: "add", text: newLine });
    }
  }
  return rows;
}
