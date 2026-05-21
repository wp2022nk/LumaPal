import type { AgentState, TodoItem } from "../types";
import {
  messageImages,
  messageText,
  parseToolCalls,
  roleOf,
} from "../utils";

export function explicitMessageId(message: any): string | null {
  const id =
    message?.message_id ??
    message?.uuid ??
    message?.lc_kwargs?.id ??
    message?.kwargs?.id ??
    (Array.isArray(message?.id) ? null : message?.id);

  return id == null || id === "" ? null : String(id);
}

function fallbackMessageKey(message: any): string {
  const toolCalls = parseToolCalls(message)
    .map((call) => `${call.id}:${call.name}`)
    .join("|");
  return [
    roleOf(message),
    messageText(message),
    messageImages(message).join("|"),
    toolCalls,
  ].join("::");
}

function messageKeysForMerge(message: any): string[] {
  return Array.from(
    new Set(
      [explicitMessageId(message), fallbackMessageKey(message)].filter(
        (key): key is string => Boolean(key),
      ),
    ),
  );
}

export function mergeMessages(
  current: unknown[] | undefined,
  incoming: unknown[],
): unknown[] {
  const next = Array.isArray(current) ? [...current] : [];
  const indexByKey = new Map<string, number>();

  next.forEach((message, index) => {
    for (const key of messageKeysForMerge(message)) {
      indexByKey.set(key, index);
    }
  });

  for (const message of incoming) {
    const keys = messageKeysForMerge(message);
    const existingIndex = keys
      .map((key) => indexByKey.get(key))
      .find((index) => index != null);
    if (existingIndex == null) {
      for (const key of keys) indexByKey.set(key, next.length);
      next.push(message);
    } else {
      next[existingIndex] = message;
      for (const key of keys) indexByKey.set(key, existingIndex);
    }
  }

  return next;
}

export function collectStatePatch(value: unknown, patch: AgentState = {}): AgentState {
  if (Array.isArray(value)) {
    for (const item of value) collectStatePatch(item, patch);
    return patch;
  }

  if (!value || typeof value !== "object") return patch;

  const objectValue = value as Record<string, unknown>;
  if (Array.isArray(objectValue.messages)) {
    patch.messages = mergeMessages(patch.messages, objectValue.messages);
  }
  if (Array.isArray(objectValue.todos)) {
    patch.todos = objectValue.todos as TodoItem[];
  }
  const interrupts = normalizeInterrupts(objectValue.__interrupt__ ?? objectValue.interrupts);
  if (interrupts.length > 0) {
    patch.__interrupt__ = interrupts;
  }

  for (const [key, childValue] of Object.entries(objectValue)) {
    if (key === "messages" || key === "todos" || key === "__interrupt__" || key === "interrupts") continue;
    collectStatePatch(childValue, patch);
  }

  return patch;
}

export function normalizeInterrupts(value: unknown): any[] {
  if (Array.isArray(value)) return value.filter(Boolean);
  if (value) return [value];
  return [];
}

export function collectInterruptsFromThreadState(state: unknown): any[] {
  if (!state || typeof state !== "object") return [];
  const objectState = state as Record<string, any>;
  const values =
    objectState.values ??
    objectState.checkpoint?.values ??
    objectState.state?.values ??
    {};
  const interrupts = normalizeInterrupts(values?.__interrupt__ ?? values?.interrupts);

  const taskLists = [
    objectState.tasks,
    objectState.checkpoint?.tasks,
    objectState.state?.tasks,
  ];
  for (const tasks of taskLists) {
    if (!Array.isArray(tasks)) continue;
    for (const task of tasks) {
      interrupts.push(...normalizeInterrupts(task?.interrupts ?? task?.__interrupt__));
    }
  }

  return interrupts;
}

export function normalizeStreamingChunk(message: any): any {
  const typeName = Array.isArray(message?.id) ? String(message.id.at(-1) ?? "") : "";
  const isAiChunk =
    message?.constructor?.name === "AIMessageChunk" ||
    typeName.toLowerCase().includes("aimessagechunk") ||
    typeName.toLowerCase().includes("ai_message_chunk");
  if (isAiChunk || roleOf(message) === "ai" || roleOf(message) === "assistant") {
    return { ...message, type: "ai", role: "ai" };
  }
  return message;
}

export function shouldFinishFromValues(values: AgentState): boolean {
  const messages = Array.isArray(values.messages) ? values.messages : [];
  const lastMessage = messages.at(-1) as any;
  const role = roleOf(lastMessage);

  if (role !== "ai" && role !== "assistant") return false;
  if (parseToolCalls(lastMessage).length > 0) return false;

  return Boolean(messageText(lastMessage).trim());
}

export function collectMessagesFromValue(value: unknown, messages: any[] = []): any[] {
  if (Array.isArray(value)) {
    for (const item of value) collectMessagesFromValue(item, messages);
    return messages;
  }

  if (!value || typeof value !== "object" || Array.isArray(value)) return messages;

  const objectValue = value as Record<string, unknown>;
  if (Array.isArray(objectValue.messages)) {
    messages.push(...objectValue.messages);
  }

  for (const [key, childValue] of Object.entries(objectValue)) {
    if (key === "messages") continue;
    collectMessagesFromValue(childValue, messages);
  }

  return messages;
}

export function latestAssistantText(messages: any[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    const role = roleOf(message);
    if ((role === "ai" || role === "assistant") && parseToolCalls(message).length === 0) {
      const text = messageText(message).trim();
      if (text) return text;
    }
  }
  return "";
}

export function toolCallIdFromMessage(message: any): string {
  return String(
    message?.tool_call_id ??
      message?.toolCallId ??
      message?.additional_kwargs?.tool_call_id ??
      message?.kwargs?.tool_call_id ??
      message?.kwargs?.toolCallId ??
      message?.kwargs?.additional_kwargs?.tool_call_id ??
      "",
  );
}

export function isLiveSubagent(subagent: any): boolean {
  return subagent?.status !== "completed" && subagent?.status !== "error";
}
