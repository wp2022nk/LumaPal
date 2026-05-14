import type { PendingImage } from "./types";

export function roleOf(message: any): string {
  return String(message?.type ?? message?.role ?? message?._getType?.() ?? "");
}

export function messageId(message: any, index = 0): string {
  return String(message?.id ?? `${roleOf(message)}-${index}`);
}

export function textFromContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((block: any) => {
        if (typeof block === "string") return block;
        if (block?.type === "text") return block.text ?? "";
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }
  if (content == null) return "";
  return String(content);
}

export function imageUrlsFromContent(content: unknown): string[] {
  if (!Array.isArray(content)) return [];
  return content
    .map((block: any) => {
      if (block?.type === "image_url") return block.image_url?.url;
      if (block?.type === "image") return block.source?.url ?? block.url;
      return null;
    })
    .filter((value): value is string => typeof value === "string" && value.length > 0);
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
