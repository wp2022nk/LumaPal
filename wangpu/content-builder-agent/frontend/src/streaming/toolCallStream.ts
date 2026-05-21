import type { ArtifactDraftView, ToolCallView } from "../types";
import { normalizeObject } from "../utils";

type ToolChunk = {
  id?: string;
  index?: number;
  name?: string;
  args?: string;
};

export function parseToolCallChunks(message: any): ToolChunk[] {
  const direct = Array.isArray(message?.tool_call_chunks)
    ? message.tool_call_chunks
    : Array.isArray(message?.kwargs?.tool_call_chunks)
      ? message.kwargs.tool_call_chunks
      : [];
  const additionalKwargs =
    message?.additional_kwargs ?? message?.kwargs?.additional_kwargs;
  const openAi = Array.isArray(additionalKwargs?.tool_calls)
    ? additionalKwargs.tool_calls.map((call: any, index: number) => ({
        id: call.id,
        index,
        name: call.function?.name,
        args: call.function?.arguments,
      }))
    : [];
  return [...direct, ...openAi].map((chunk: any, index: number) => ({
    id: chunk.id ?? chunk.call_id,
    index: Number(chunk.index ?? index),
    name: chunk.name ?? chunk.function?.name,
    args: typeof chunk.args === "string"
      ? chunk.args
      : typeof chunk.function?.arguments === "string"
        ? chunk.function.arguments
        : "",
  }));
}

function readStringField(args: Record<string, unknown>, names: string[]): string | undefined {
  for (const name of names) {
    const value = args[name];
    if (typeof value === "string" && value) return value;
  }
  return undefined;
}

function maybeParseArgs(argsText: string): Record<string, unknown> | null {
  try {
    return normalizeObject(argsText);
  } catch {
    return null;
  }
}

export function draftFromToolCall(call: ToolCallView): ArtifactDraftView | null {
  if (!["write_file", "edit_file"].includes(call.name)) return null;
  const path = readStringField(call.args, ["file_path", "path"]);
  const content = readStringField(call.args, ["content", "new_content", "text", "replacement"]) ?? "";
  return {
    id: call.id,
    toolName: call.name,
    status: "finished",
    path,
    content,
    argsText: JSON.stringify(call.args, null, 2),
    updatedAt: new Date().toISOString(),
  };
}

export function updateDraftFromArgsText(
  existing: ArtifactDraftView | undefined,
  patch: {
    id: string;
    toolName: string;
    argsText: string;
    messageId?: string;
    subagentId?: string;
    status?: ArtifactDraftView["status"];
  },
): ArtifactDraftView {
  const parsed = maybeParseArgs(patch.argsText);
  const path = parsed ? readStringField(parsed, ["file_path", "path"]) : existing?.path;
  const content = parsed
    ? readStringField(parsed, ["content", "new_content", "text", "replacement"])
    : undefined;
  return {
    id: patch.id,
    toolName: patch.toolName,
    status: patch.status ?? existing?.status ?? "streaming",
    path,
    content: content ?? existing?.content ?? "",
    argsText: patch.argsText,
    messageId: patch.messageId ?? existing?.messageId,
    subagentId: patch.subagentId ?? existing?.subagentId,
    updatedAt: new Date().toISOString(),
  };
}
