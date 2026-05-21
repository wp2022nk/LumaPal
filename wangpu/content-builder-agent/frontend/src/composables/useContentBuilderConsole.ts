import { computed, onMounted, reactive, ref, watch } from "vue";
import { fetchAgent, getArtifacts, getFile, getManifest, getTree } from "../api";
import type {
  ActiveInspector,
  AgentManifest,
  AgentState,
  ArtifactDraftView,
  ArtifactItem,
  FilePayload,
  PendingImage,
  SessionRecord,
  SubagentRunView,
  SubagentStepView,
  TodoItem,
  ToolEventView,
  ToolManifest,
  WorkspaceEntry,
} from "../types";
import {
  fileToPendingImage,
  messageId,
  messageImages,
  messageText,
  normalizeObject,
  parseToolCalls,
  readableError,
  roleOf,
  simpleLineDiff,
} from "../utils";
import {
  collectInterruptsFromThreadState,
  collectMessagesFromValue,
  collectStatePatch,
  explicitMessageId,
  isLiveSubagent,
  latestAssistantText,
  mergeMessages,
  normalizeInterrupts,
  normalizeStreamingChunk,
  shouldFinishFromValues,
  toolCallIdFromMessage,
} from "../streaming/messageState";
import {
  isPlainObject,
  isSubagentNamespace,
  iterateSseEvents,
  namespaceSource,
  normalizeStreamPayloads,
  splitStreamEventName,
} from "../streaming/sse";
import {
  draftFromToolCall,
  parseToolCallChunks,
  updateDraftFromArgsText,
} from "../streaming/toolCallStream";

const ASSISTANT_ID = "content_writer";
const STORAGE_SESSIONS = "content-builder:sessions";
const STORAGE_THREAD = "content-builder:active-thread";
const STORAGE_RUN = "content-builder:active-run";
const STORAGE_INSPECTOR_WIDTH = "content-builder:inspector-width";
const DEFAULT_SESSION_TITLE = "新对话";


function loadSessions(): SessionRecord[] {
  try {
    const raw = localStorage.getItem(STORAGE_SESSIONS);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function useContentBuilderConsole() {
  const activeThreadId = ref<string | null>(localStorage.getItem(STORAGE_THREAD));
  const streamThreadId = ref<string | null>(activeThreadId.value);
  const savedRunId = ref<string | null>(localStorage.getItem(STORAGE_RUN));
  const historyMessages = ref<any[] | null>(null);
  const optimisticMessage = ref<any | null>(null);
  const draft = ref("");
  const pendingImages = ref<PendingImage[]>([]);
  const streamError = ref<string | null>(null);
  const inspectorWidth = ref(
    Number(localStorage.getItem(STORAGE_INSPECTOR_WIDTH)) || 430,
  );

  const manifest = ref<AgentManifest | null>(null);
  const manifestError = ref<string | null>(null);
  const artifacts = ref<ArtifactItem[]>([]);
  const artifactError = ref<string | null>(null);
  const selectedArtifact = ref<ArtifactItem | null>(null);
  const artifactPayload = ref<FilePayload | null>(null);
  const workspaceEntries = ref<WorkspaceEntry[]>([]);
  const workspaceError = ref<string | null>(null);
  const selectedFile = ref<FilePayload | null>(null);
  const fileBaselines = reactive<Record<string, string>>({});
  const activeInspector = ref<ActiveInspector>("plan");
  const collapsed = reactive<Record<string, boolean>>({
    manifestTools: false,
    manifestSkills: false,
    manifestSubagents: false,
    plan: false,
    flow: false,
    artifacts: false,
    sandbox: false,
  });

  const sessions = ref<SessionRecord[]>(loadSessions());

  function saveSessions() {
    localStorage.setItem(STORAGE_SESSIONS, JSON.stringify(sessions.value));
  }

  function upsertSession(id: string, title?: string) {
    const now = new Date().toISOString();
    const existing = sessions.value.find((session) => session.id === id);
    if (existing) {
      existing.updatedAt = now;
      if (title && existing.title.startsWith(DEFAULT_SESSION_TITLE)) {
        existing.title = title;
      }
    } else {
      sessions.value.unshift({
        id,
        title: title || `${DEFAULT_SESSION_TITLE} ${sessions.value.length + 1}`,
        createdAt: now,
        updatedAt: now,
      });
    }
    saveSessions();
  }

  const streamValues = ref<AgentState>({});
  const streamMessagesState = ref<any[]>([]);
  const streamToolCalls = ref<any[]>([]);
  const streamInterrupts = ref<any[]>([]);
  const streamIsLoading = ref(false);
  const streamIsThreadLoading = ref(false);
  const streamSdkError = ref<unknown>(null);
  const streamSubagents = ref(new Map<string, any>());
  const streamSubgraphs = ref(new Map<string, any>());
  const streamArtifactDrafts = ref(new Map<string, ArtifactDraftView>());
  const streamingContentById = new Map<string, string>();
  const artifactRevealTimers = new Map<string, number>();
  const streamingToolCallsById = new Map<
    string,
    {
      id: string;
      index: number;
      name: string;
      argsText: string;
      messageId?: string;
      subagentId?: string;
    }
  >();
  let activeRunAbortController: AbortController | null = null;

  function resetLiveStreamState(values: AgentState = {}) {
    streamValues.value = values;
    streamMessagesState.value = Array.isArray(values.messages)
      ? (values.messages as any[])
      : [];
    streamToolCalls.value = [];
    streamInterrupts.value = [];
    streamSdkError.value = null;
    streamSubagents.value = new Map();
    streamSubgraphs.value = new Map();
    streamArtifactDrafts.value = new Map();
    streamingContentById.clear();
    for (const timer of artifactRevealTimers.values()) window.clearInterval(timer);
    artifactRevealTimers.clear();
    streamingToolCallsById.clear();
  }

  function upsertArtifactDraft(draft: ArtifactDraftView) {
    const current = streamArtifactDrafts.value.get(draft.id);
    const currentContent = current?.content ?? "";
    const shouldReveal =
      draft.content.length > currentContent.length + 40 &&
      draft.status !== "streaming";

    if (!shouldReveal) {
      streamArtifactDrafts.value = new Map(streamArtifactDrafts.value).set(draft.id, draft);
      return;
    }

    const visibleDraft = {
      ...draft,
      status: "streaming" as const,
      content: currentContent,
    };
    streamArtifactDrafts.value = new Map(streamArtifactDrafts.value).set(draft.id, visibleDraft);

    const existingTimer = artifactRevealTimers.get(draft.id);
    if (existingTimer) window.clearInterval(existingTimer);

    let cursor = currentContent.length;
    const timer = window.setInterval(() => {
      cursor = Math.min(draft.content.length, cursor + 8);
      const next = {
        ...draft,
        status: cursor >= draft.content.length ? draft.status : ("streaming" as const),
        content: draft.content.slice(0, cursor),
        updatedAt: new Date().toISOString(),
      };
      streamArtifactDrafts.value = new Map(streamArtifactDrafts.value).set(draft.id, next);
      if (cursor >= draft.content.length) {
        window.clearInterval(timer);
        artifactRevealTimers.delete(draft.id);
      }
    }, 24);
    artifactRevealTimers.set(draft.id, timer);
  }

  function upsertSubagent(patch: Partial<SubagentRunView>) {
    const id =
      patch.id ??
      (patch.source ? `subgraph:${patch.source}` : `subagent:${Date.now()}`);
    const current = streamSubagents.value.get(id) ?? { id, status: "pending" };
    streamSubagents.value = new Map(streamSubagents.value).set(id, {
      ...current,
      ...patch,
      id,
      updatedAt: new Date().toISOString(),
    });
    return id;
  }

  function upsertSubagentStep(subagentId: string, step: SubagentStepView) {
    const current = streamSubagents.value.get(subagentId) ?? {
      id: subagentId,
      status: "running",
    };
    const steps = Array.isArray(current.steps) ? [...current.steps] : [];
    const index = steps.findIndex((item: SubagentStepView) => item.id === step.id);
    if (index >= 0) {
      steps[index] = { ...steps[index], ...step };
    } else {
      steps.push(step);
    }
    upsertSubagent({ id: subagentId, steps, status: current.status ?? "running" });
  }

  function activeSubagentIdForName(name: string | null): string | null {
    if (!name) return null;
    for (const [id, subagent] of streamSubagents.value.entries()) {
      const sameName =
        subagent.name === name ||
        subagent.toolCall?.args?.subagent_type === name ||
        subagent.toolCall?.args?.subagent_name === name;
      if (sameName && isLiveSubagent(subagent)) {
        return id;
      }
    }
    return null;
  }

  function activeTaskSubagent(): [string, any] | null {
    for (const entry of streamSubagents.value.entries()) {
      const [, subagent] = entry;
      if (subagent.toolCall?.name === "task" && isLiveSubagent(subagent)) return entry;
    }
    return null;
  }

  function resolveSubagentTarget(namespace: string[], metadata?: Record<string, unknown>) {
    const source = namespaceSource(namespace) ?? "subagent";
    const taskSubagent = activeTaskSubagent();
    if (taskSubagent) {
      const [id, subagent] = taskSubagent;
      return {
        id,
        name: String(subagent.name ?? subagent.toolCall?.args?.subagent_type ?? source),
        source,
      };
    }

    const knownSubagent = manifest.value?.subagents.some((subagent) => subagent.name === source);
    const activeId = activeSubagentIdForName(source);
    if (!knownSubagent && !activeId) return null;

    const id =
      activeId ??
      String(metadata?.langgraph_checkpoint_ns ?? metadata?.run_id ?? `subgraph:${namespace.join("/") || source}`);
    const existing = streamSubagents.value.get(id);
    return {
      id,
      name: String(existing?.name ?? source),
      source,
    };
  }

  function subagentNameFromToolCall(call: { name: string; args: Record<string, unknown> }) {
    if (call.name !== "task") return null;
    return String(
      call.args.subagent_type ??
        call.args.subagent_name ??
        call.args.name ??
        call.args.agent ??
        "subagent",
    );
  }

  function taskInputFromToolCall(call: { args: Record<string, unknown> }) {
    return String(
      call.args.description ??
        call.args.task ??
        call.args.prompt ??
        call.args.input ??
        "",
    );
  }

  function trackArtifactDraftsFromMessage(message: any, subagentId?: string) {
    for (const call of parseToolCalls(message)) {
      const draft = draftFromToolCall(call);
      if (!draft) continue;
      upsertArtifactDraft({
        ...draft,
        messageId: messageId(message),
        subagentId,
      });
    }
  }

  function trackStreamingToolCallChunks(
    chunk: any,
    metadata: Record<string, unknown> | undefined,
    namespace: string[],
    subagentId?: string,
  ) {
    const chunks = parseToolCallChunks(chunk);
    if (!chunks.length) return;

    const sourceId =
      explicitMessageId(chunk) ??
      String(metadata?.run_id ?? namespace.join("/") ?? "stream");
    for (const toolChunk of chunks) {
      const key = `${sourceId}:${toolChunk.index ?? 0}`;
      const existing = streamingToolCallsById.get(key);
      const name = toolChunk.name ?? existing?.name ?? "";
      const argsText = `${existing?.argsText ?? ""}${toolChunk.args ?? ""}`;
      const assembled = {
        id: String(toolChunk.id ?? existing?.id ?? key),
        index: toolChunk.index ?? existing?.index ?? 0,
        name,
        argsText,
        messageId: sourceId,
        subagentId,
      };
      streamingToolCallsById.set(key, assembled);

      streamToolCalls.value = Array.from(streamingToolCallsById.values())
        .filter((call) => call.name)
        .map((call) => ({
          callId: call.id,
          name: call.name,
          input: call.argsText,
          messageId: call.messageId,
        }));

      if (["write_file", "edit_file"].includes(name)) {
        upsertArtifactDraft(
          updateDraftFromArgsText(streamArtifactDrafts.value.get(assembled.id), {
            id: assembled.id,
            toolName: name,
            argsText,
            messageId: sourceId,
            subagentId,
            status: "streaming",
          }),
        );
      }
    }
  }

  function trackSubagentToolCalls(message: any) {
    trackArtifactDraftsFromMessage(message);
    for (const call of parseToolCalls(message)) {
      const name = subagentNameFromToolCall(call);
      if (!name) continue;
      upsertSubagent({
        id: call.id,
        name,
        status: "running",
        taskInput: taskInputFromToolCall(call),
        toolCall: call,
        parentMessageId: messageId(message),
      });
    }

    if (roleOf(message) !== "tool") return;
    const toolCallId = toolCallIdFromMessage(message);
    if (!toolCallId || !streamSubagents.value.has(toolCallId)) return;
    upsertSubagent({
      id: toolCallId,
      status: (message as any).status === "error" ? "error" : "completed",
      result: messageText(message),
    });
  }

  function trackSubagentSteps(subagentId: string, messages: any[]) {
    for (const message of messages) {
      trackArtifactDraftsFromMessage(message, subagentId);
      for (const call of parseToolCalls(message)) {
        upsertSubagentStep(subagentId, {
          id: call.id,
          name: call.name,
          status: "running",
          args: call.args,
        });
      }

      if (roleOf(message) !== "tool") continue;
      const id = toolCallIdFromMessage(message);
      if (!id) continue;
      upsertSubagentStep(subagentId, {
        id,
        name: String((message as any).name ?? (message as any).kwargs?.name ?? "tool"),
        status: (message as any).status === "error" ? "error" : "completed",
        result: messageText(message),
      });
    }
  }

  function subagentIdFromNamespace(namespace: string[], metadata?: Record<string, unknown>) {
    return resolveSubagentTarget(namespace, metadata)?.id ?? `subgraph:${namespace.join("/") || "subagent"}`;
  }

  function appendSubagentOutput(
    namespace: string[],
    metadata: Record<string, unknown> | undefined,
    chunk: any,
  ) {
    const text = messageText(chunk);
    const hasToolCalls =
      parseToolCalls(chunk).length > 0 ||
      (Array.isArray(chunk?.tool_call_chunks) && chunk.tool_call_chunks.length > 0);
    if (!text && !hasToolCalls) return;

    const target = resolveSubagentTarget(namespace, metadata);
    if (!target) return;
    const { id, name, source } = target;
    trackStreamingToolCallChunks(chunk, metadata, namespace, id);
    const streamId = `subagent:${id}:${explicitMessageId(chunk) ?? metadata?.run_id ?? source}`;
    const previousText = streamingContentById.get(streamId) ?? "";
    const nextText = text ? `${previousText}${text}` : previousText;
    streamingContentById.set(streamId, nextText);

    upsertSubagent({
      id,
      name,
      source,
      status: "running",
      output: nextText,
    });

    if (chunk?.chunk_position === "last") {
      streamingContentById.delete(streamId);
    }
  }

  function applySubagentUpdate(namespace: string[], data: unknown) {
    if (!isSubagentNamespace(namespace)) return;

    const target = resolveSubagentTarget(namespace);
    if (!target) return;
    const { id, name, source } = target;
    const messages = collectMessagesFromValue(data);
    for (const message of messages) trackSubagentToolCalls(message);
    trackSubagentSteps(id, messages);

    const output = latestAssistantText(messages);
    upsertSubagent({
      id,
      name,
      source,
      status: "running",
      ...(output ? { output } : {}),
    });

    streamSubgraphs.value = new Map(streamSubgraphs.value).set(id, {
      id,
      source,
      namespace,
      data,
    });
  }

  function applyTaskEvent(namespace: string[], data: unknown) {
    if (!isPlainObject(data)) return;
    const source = namespaceSource(namespace);
    const taskName = String(data.name ?? source ?? "task");
    const taskId = String(data.id ?? "");
    const isSubagentTask = source && source !== "main";
    if (!isSubagentTask && taskName !== "task") return;

    const target = isSubagentTask ? resolveSubagentTarget(namespace) : null;
    if (isSubagentTask && !target) return;
    const name = target?.name ?? source ?? taskName;
    const id = target?.id ?? activeSubagentIdForName(name) ?? (taskId ? `task:${taskId}` : `task:${name}`);
    upsertSubagent({
      id,
      name,
      source: source ?? name,
      status: data.error ? "error" : "running",
      ...(isPlainObject(data.input) ? { taskInput: JSON.stringify(data.input, null, 2) } : {}),
      ...(data.result ? { result: data.result } : {}),
      ...(data.error ? { error: data.error } : {}),
    });
  }

  async function readResponseError(response: Response): Promise<string> {
    try {
      return await response.text();
    } catch {
      return `${response.status} ${response.statusText}`;
    }
  }

  async function fetchThreadState(threadId: string): Promise<any> {
    const response = await fetchAgent(`/threads/${threadId}/state`);
    if (!response.ok) {
      throw new Error(`Failed to read thread state: ${response.status} ${response.statusText}`);
    }
    return response.json();
  }

  function syncInterruptsFromThreadState(state: unknown) {
    const interrupts = collectInterruptsFromThreadState(state);
    if (interrupts.length > 0) {
      streamInterrupts.value = interrupts;
    }
  }

  async function refreshInterruptsFromThreadState(threadId: string) {
    try {
      syncInterruptsFromThreadState(await fetchThreadState(threadId));
    } catch {
      // Streaming already surfaced the primary run result. A state refresh is only
      // a fallback for interrupts that were not present in the SSE frames.
    }
  }

  async function createThread(): Promise<string> {
    const response = await fetchAgent("/threads", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{}",
    });

    if (!response.ok) {
      throw new Error(`创建会话失败: ${await readResponseError(response)}`);
    }

    const data = await response.json();
    const threadId = data?.thread_id;
    if (typeof threadId !== "string" || !threadId) {
      throw new Error("创建会话失败: 后端没有返回 thread_id");
    }

    return threadId;
  }

  function setActiveThread(threadId: string) {
    streamThreadId.value = threadId;
    activeThreadId.value = threadId;
    localStorage.setItem(STORAGE_THREAD, threadId);
    upsertSession(threadId);
  }

  async function syncStreamThread(threadId: string | null) {
    const changed = streamThreadId.value !== threadId;
    streamThreadId.value = threadId;
    activeThreadId.value = threadId;
    if (changed || threadId == null) resetLiveStreamState();
  }

  async function prepareThreadForSubmit() {
    if (activeThreadId.value) {
      streamThreadId.value = activeThreadId.value;
      return;
    }

    const threadId = await createThread();
    setActiveThread(threadId);
  }

  function prepareMessageForDisplay(message: any): any {
    return message;
  }

  function applyStatePatch(patch: AgentState) {
    const nextValues: AgentState = { ...streamValues.value, ...patch };

    if (Array.isArray(patch.messages)) {
      const displayMessages = patch.messages.map((message) =>
        prepareMessageForDisplay(message),
      );
      streamMessagesState.value = mergeMessages(
        streamMessagesState.value,
        displayMessages,
      ) as any[];
      for (const message of patch.messages) trackSubagentToolCalls(message);
      nextValues.messages = streamMessagesState.value;
    }

    if ("__interrupt__" in patch) {
      streamInterrupts.value = normalizeInterrupts((patch as any).__interrupt__);
    }

    streamValues.value = nextValues;
  }

  function applyStreamingMessageEvent(
    data: unknown,
    namespace: string[] = [],
    metadata?: Record<string, unknown>,
  ) {
    if (!Array.isArray(data) || !data[0]) return;

    const chunk = normalizeStreamingChunk(data[0]);
    const meta = metadata ?? (isPlainObject(data[1]) ? data[1] : undefined);
    if (isSubagentNamespace(namespace)) {
      appendSubagentOutput(namespace, meta, chunk);
      return;
    }

    trackStreamingToolCallChunks(chunk, meta, namespace);

    const id =
      explicitMessageId(chunk) ??
      String((data[1] as any)?.run_id ?? `stream-${Date.now()}`);
    const text = messageText(chunk);
    const previousText = streamingContentById.get(id) ?? "";
    const nextText = text ? `${previousText}${text}` : previousText;
    const hasToolCalls =
      parseToolCalls(chunk).length > 0 ||
      (Array.isArray(chunk?.tool_call_chunks) && chunk.tool_call_chunks.length > 0);

    if (!nextText && !hasToolCalls) return;

    streamingContentById.set(id, nextText);
    const message = {
      ...chunk,
      id,
      type: ["ai", "assistant"].includes(roleOf(chunk)) ? "ai" : roleOf(chunk) || "ai",
      role: ["ai", "assistant"].includes(roleOf(chunk)) ? "ai" : roleOf(chunk) || "ai",
      content: nextText || messageText(chunk),
    };

    applyStatePatch({ messages: [message] });

    if (chunk?.chunk_position === "last") {
      streamingContentById.delete(id);
    }
  }

  function applyLowLevelEvent(data: unknown, namespace: string[]) {
    if (!isPlainObject(data)) return;
    const eventName = String(data.event ?? "");
    const eventData = isPlainObject(data.data) ? data.data : {};
    const metadata = isPlainObject(data.metadata) ? data.metadata : undefined;

    if (eventName === "on_chat_model_stream" && eventData.chunk) {
      const eventNamespace =
        namespace.length > 0
          ? namespace
          : typeof metadata?.langgraph_checkpoint_ns === "string"
            ? String(metadata.langgraph_checkpoint_ns).split("|").filter(Boolean)
            : [];
      applyStreamingMessageEvent([eventData.chunk, metadata ?? {}], eventNamespace, metadata);
      return;
    }

    if (eventName === "on_tool_start" || eventName === "on_tool_end") {
      const name = String(data.name ?? eventData.name ?? "");
      if (!["write_file", "edit_file"].includes(name)) return;
      const input = eventName === "on_tool_start" ? eventData.input : eventData.output;
      upsertArtifactDraft(
        updateDraftFromArgsText(streamArtifactDrafts.value.get(String(data.run_id ?? name)), {
          id: String(data.run_id ?? name),
          toolName: name,
          argsText: typeof input === "string" ? input : JSON.stringify(input ?? {}, null, 2),
          status: eventName === "on_tool_start" ? "streaming" : "finished",
        }),
      );
    }
  }

  function applyToolStreamEvent(data: unknown) {
    if (!isPlainObject(data)) return;
    const name = String(data.name ?? "");
    if (!["write_file", "edit_file"].includes(name)) return;
    const id = String(data.toolCallId ?? data.name ?? "tool");
    const payload = data.input ?? data.data ?? data.result ?? data.output ?? data.error ?? {};
    upsertArtifactDraft(
      updateDraftFromArgsText(streamArtifactDrafts.value.get(id), {
        id,
        toolName: name,
        argsText: typeof payload === "string" ? payload : JSON.stringify(payload, null, 2),
        status: data.event === "on_tool_end" ? "finished" : data.event === "on_tool_error" ? "error" : "streaming",
      }),
    );
  }

  function finishRun(abortController: AbortController) {
    if (activeRunAbortController !== abortController) return;
    activeRunAbortController = null;
    streamIsLoading.value = false;
    savedRunId.value = null;
    localStorage.removeItem(STORAGE_RUN);
  }

  async function submitRun(input: Partial<AgentState> | null | undefined, options?: any) {
    await prepareThreadForSubmit();
    const threadId = activeThreadId.value;
    if (!threadId) throw new Error("无法提交消息: 当前没有可用的 thread_id");

    activeRunAbortController?.abort();
    const abortController = new AbortController();
    activeRunAbortController = abortController;
    streamIsLoading.value = true;
    streamSdkError.value = null;

    if (typeof options?.optimisticValues === "function") {
      applyStatePatch(options.optimisticValues(streamValues.value));
    }

    const payload: Record<string, unknown> = {
      assistant_id: ASSISTANT_ID,
      stream_mode: ["messages-tuple", "values", "updates", "tasks"],
      stream_subgraphs: true,
      multitask_strategy: options?.multitaskStrategy ?? "rollback",
      config: {
        ...(options?.config ?? {}),
        configurable: {
          ...((options?.config as any)?.configurable ?? {}),
          thread_id: threadId,
        },
      },
    };

    if (options?.command) {
      payload.command = options.command;
    } else {
      payload.input = input ?? null;
    }

    try {
      const response = await fetchAgent(`/threads/${threadId}/runs/stream`, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          accept: "text/event-stream",
        },
        body: JSON.stringify(payload),
        signal: abortController.signal,
      });

      if (!response.ok) {
        throw new Error(
          `流式运行失败: ${response.status} ${response.statusText}: ${await readResponseError(response)}`,
        );
      }

      for await (const event of iterateSseEvents(response)) {
        if (abortController.signal.aborted) break;
        const { baseEvent, namespace: eventNamespace } = splitStreamEventName(event.event);
        const payloads = normalizeStreamPayloads(baseEvent, event.data, eventNamespace);

        if (baseEvent === "metadata" && event.data && typeof event.data === "object") {
          const runId = (event.data as any).run_id;
          if (typeof runId === "string") {
            savedRunId.value = runId;
            localStorage.setItem(STORAGE_RUN, runId);
          }
          continue;
        }

        if (baseEvent === "messages") {
          for (const payload of payloads) {
            applyStreamingMessageEvent(payload.data, payload.namespace, payload.metadata);
          }
          continue;
        }

        if (baseEvent === "messages/partial" || baseEvent === "messages/complete") {
          for (const payload of payloads) {
            if (Array.isArray(payload.data)) {
              if (isSubagentNamespace(payload.namespace)) {
                applySubagentUpdate(payload.namespace, { messages: payload.data });
                continue;
              }
              applyStatePatch({ messages: payload.data });
            }
          }
          continue;
        }

        if (baseEvent === "values") {
          for (const payload of payloads) {
            if (isSubagentNamespace(payload.namespace)) {
              applySubagentUpdate(payload.namespace, payload.data);
              continue;
            }
            const patch = collectStatePatch(payload.data);
            applyStatePatch(patch);
            if (shouldFinishFromValues(patch)) break;
          }
          continue;
        }

        if (baseEvent === "updates") {
          for (const payload of payloads) {
            applySubagentUpdate(payload.namespace, payload.data);
            if (isSubagentNamespace(payload.namespace)) continue;
            applyStatePatch(collectStatePatch(payload.data));
          }
          continue;
        }

        if (baseEvent === "tasks") {
          for (const payload of payloads) {
            applyTaskEvent(payload.namespace, payload.data);
            if (isSubagentNamespace(payload.namespace)) continue;
            applyStatePatch(collectStatePatch(payload.data));
          }
          continue;
        }

        if (baseEvent === "events") {
          for (const payload of payloads) {
            applyLowLevelEvent(payload.data, payload.namespace);
          }
          continue;
        }

        if (baseEvent === "tools") {
          for (const payload of payloads) {
            applyToolStreamEvent(payload.data);
          }
          continue;
        }

        if (baseEvent === "error") {
          throw new Error(readableError(event.data));
        }

        if (baseEvent === "end") break;
      }
      if (!abortController.signal.aborted && streamInterrupts.value.length === 0) {
        await refreshInterruptsFromThreadState(threadId);
      }
    } catch (error) {
      if (!abortController.signal.aborted) {
        streamSdkError.value = error;
        streamError.value = readableError(error);
        options?.onError?.(error);
        throw error;
      }
    } finally {
      finishRun(abortController);
    }
  }

  async function stopActiveRun() {
    activeRunAbortController?.abort();
    activeRunAbortController = null;
    streamIsLoading.value = false;
    savedRunId.value = null;
    localStorage.removeItem(STORAGE_RUN);
  }

  const stream = {
    values: streamValues,
    messages: streamMessagesState,
    toolCalls: streamToolCalls,
    interrupts: streamInterrupts,
    interrupt: computed(() => streamInterrupts.value[0]),
    isLoading: computed(() => streamIsLoading.value),
    isThreadLoading: computed(() => streamIsThreadLoading.value),
    error: computed(() => streamSdkError.value),
    threadId: computed(() => activeThreadId.value),
    hydrationPromise: computed(() => Promise.resolve()),
    subagents: streamSubagents,
    subgraphs: streamSubgraphs,
    submit: submitRun,
    stop: stopActiveRun,
    respond: async (response: unknown) =>
      submitRun(null, { command: { resume: response } }),
    getThread: () => undefined,
    client: null,
    assistantId: ASSISTANT_ID,
  } as any;

  function extractStreamMessages(): any[] {
    const direct = stream.messages.value;
    if (Array.isArray(direct) && direct.length > 0) return direct;

    const values = stream.values.value as AgentState;
    if (Array.isArray(values?.messages) && values.messages.length > 0) {
      return values.messages;
    }

    return [];
  }

  function optimisticEchoedIn(messages: any[]): boolean {
    if (!optimisticMessage.value) return false;

    const expectedRole = roleOf(optimisticMessage.value);
    const expectedText = messageText(optimisticMessage.value).trim();
    const expectedImages = messageImages(optimisticMessage.value);

    return messages.some((message) => {
      if (roleOf(message) !== expectedRole) return false;

      const text = messageText(message).trim();
      if (expectedText && text === expectedText) return true;

      if (!expectedText && expectedImages.length > 0) {
        const images = messageImages(message);
        return expectedImages.every((url) => images.includes(url));
      }

      return false;
    });
  }

  const streamMessages = computed(() => {
    const next = extractStreamMessages();
    if (!activeThreadId.value && !stream.isLoading.value) return [];
    return next;
  });

  watch(
    streamMessages,
    (next) => {
      if (next.length > 0) historyMessages.value = null;
    },
    { deep: true },
  );

  watch(
    streamMessages,
    (next) => {
      if (optimisticEchoedIn(next)) optimisticMessage.value = null;
    },
    { deep: true },
  );

  const messages = computed(() => {
    const base =
      streamMessages.value.length > 0
        ? streamMessages.value
        : historyMessages.value ?? [];
    if (!optimisticMessage.value) return base;
    if (optimisticEchoedIn(base)) return base;
    return [...base, optimisticMessage.value];
  });

  const toolByName = computed(() => {
    const map = new Map<string, ToolManifest>();
    for (const tool of manifest.value?.tools ?? []) {
      map.set(tool.name, tool);
    }
    return map;
  });

  const subagentByName = computed(() => {
    const map = new Map<string, string>();
    for (const subagent of manifest.value?.subagents ?? []) {
      map.set(subagent.name, subagent.description);
    }
    return map;
  });

  const chatMessages = computed(() =>
    messages.value.filter((message, index) => {
      const role = roleOf(message);
      const text = messageText(message);
      return (
        ["human", "ai", "user", "assistant"].includes(role) &&
        (text ||
          messageImages(message).length ||
          parseToolCalls(message).length ||
          index === messages.value.length - 1 ||
          stream.isLoading.value)
      );
    }),
  );

  const todos = computed<TodoItem[]>(() => {
    const values = stream.values.value as AgentState;
    return Array.isArray(values?.todos) ? values.todos : [];
  });

  const todoStats = computed(() => {
    const total = todos.value.length;
    const completed = todos.value.filter((todo) => todo.status === "completed").length;
    const active = todos.value.filter((todo) => todo.status === "in_progress").length;
    return {
      total,
      completed,
      active,
      percent: total ? Math.round((completed / total) * 100) : 0,
    };
  });

  const subagents = computed(() => {
    const raw = (stream as any).subagents;
    const map = raw?.value ?? raw;
    const merged = new Map<string, any>(map?.entries ? Array.from(map.entries()) : []);

    for (const message of messages.value) {
      for (const call of parseToolCalls(message)) {
        const name = subagentNameFromToolCall(call);
        if (!name) continue;
        const existing = merged.get(call.id);
        const result = messages.value.find(
          (candidate) =>
            roleOf(candidate) === "tool" && toolCallIdFromMessage(candidate) === call.id,
        );
        merged.set(call.id, {
          ...(existing ?? {}),
          id: call.id,
          name,
          status: result
            ? (result as any).status === "error"
              ? "error"
              : "completed"
            : existing?.status ?? "running",
          taskInput: taskInputFromToolCall(call),
          toolCall: call,
          parentMessageId: messageId(message),
          ...(result ? { result: messageText(result) } : {}),
        });
      }
    }

    return Array.from(merged.values()).filter(
      (subagent: any) =>
        subagent.toolCall?.name === "task" ||
        manifest.value?.subagents.some((item) => item.name === subagent.name),
    );
  });

  const artifactDrafts = computed(() =>
    Array.from(streamArtifactDrafts.value.values()).sort((left, right) =>
      left.updatedAt.localeCompare(right.updatedAt),
    ),
  );

  const toolResultById = computed(() => {
    const map = new Map<string, { content: string; status?: string }>();
    for (const message of messages.value) {
      if (roleOf(message) !== "tool") continue;
      const toolCallId = String(
        (message as any).tool_call_id ??
          (message as any).toolCallId ??
          (message as any).additional_kwargs?.tool_call_id ??
          (message as any).kwargs?.tool_call_id ??
          (message as any).kwargs?.toolCallId ??
          (message as any).kwargs?.additional_kwargs?.tool_call_id ??
          "",
      );
      if (!toolCallId) continue;
      map.set(toolCallId, {
        content: messageText(message),
        status: (message as any).status,
      });
    }
    return map;
  });

  const toolEvents = computed<ToolEventView[]>(() => {
    const events: ToolEventView[] = [];
    const seen = new Set<string>();
    for (const message of messages.value) {
      for (const call of parseToolCalls(message)) {
        const id = call.id || `${messageId(message)}:${call.name}`;
        seen.add(id);
        events.push({
          id,
          call,
          result: toolResultById.value.get(id),
          tool: toolByName.value.get(call.name),
          messageId: messageId(message),
          running: !toolResultById.value.has(id),
        });
      }
    }
    for (const assembled of (stream as any).toolCalls?.value ?? []) {
      const id = String((assembled as any).callId ?? "");
      if (!id || seen.has(id)) continue;
      events.push({
        id,
        call: {
          id,
          name: String((assembled as any).name ?? "tool"),
          args: normalizeObject((assembled as any).input),
        },
        tool: toolByName.value.get(String((assembled as any).name ?? "")),
        running: true,
      });
    }
    return events;
  });

  const completedMutatingTools = computed(() =>
    toolEvents.value
      .filter((event) => event.result && event.tool?.mutates_workspace)
      .map((event) => `${event.id}:${event.result?.status ?? "ok"}`)
      .join("|"),
  );

  const fileDiff = computed(() => {
    if (!selectedFile.value || selectedFile.value.encoding !== "text") return [];
    const baseline = fileBaselines[selectedFile.value.path] ?? selectedFile.value.content;
    return simpleLineDiff(baseline, selectedFile.value.content);
  });

  const selectedArtifactKind = computed(
    () => selectedArtifact.value?.kind ?? "binary",
  );

  function callsForMessage(message: any): ToolEventView[] {
    const ids = new Set(parseToolCalls(message).map((call) => call.id));
    return toolEvents.value.filter((event) => ids.has(event.id));
  }

  function subagentsForMessage(message: any): any[] {
    const getSubagents = (stream as any).getSubagentsByMessage;
    if (typeof getSubagents === "function") {
      return getSubagents(messageId(message)) ?? [];
    }
    const ids = new Set(parseToolCalls(message).map((call) => call.id));
    return subagents.value.filter((subagent: any) => ids.has(String(subagent.id)));
  }

  function artifactDraftsForMessage(message: any): ArtifactDraftView[] {
    const messageIds = new Set([
      messageId(message),
      ...parseToolCalls(message).map((call) => call.id),
    ]);
    const subagentIds = new Set(
      subagentsForMessage(message).map((subagent: any) => String(subagent.id)),
    );
    return artifactDrafts.value.filter(
      (draft) =>
        (draft.messageId && messageIds.has(draft.messageId)) ||
        (draft.id && messageIds.has(draft.id)) ||
        (draft.subagentId && subagentIds.has(draft.subagentId)),
    );
  }

  function syncCollapsed(key: string, event: Event) {
    collapsed[key] = !(event.target as HTMLDetailsElement).open;
  }

  async function loadManifest() {
    try {
      manifestError.value = null;
      manifest.value = await getManifest();
    } catch (error) {
      manifestError.value = readableError(error);
    }
  }

  async function loadArtifacts() {
    try {
      artifactError.value = null;
      artifacts.value = await getArtifacts();
      if (selectedArtifact.value) {
        selectedArtifact.value =
          artifacts.value.find((item) => item.path === selectedArtifact.value?.path) ??
          selectedArtifact.value;
      }
    } catch (error) {
      artifactError.value = readableError(error);
    }
  }

  async function loadWorkspace() {
    try {
      workspaceError.value = null;
      workspaceEntries.value = await getTree(".", 3);
    } catch (error) {
      workspaceError.value = readableError(error);
    }
  }

  async function refreshSideData() {
    await Promise.all([loadManifest(), loadArtifacts(), loadWorkspace()]);
  }

  async function openWorkspaceFile(entry: WorkspaceEntry) {
    if (entry.type !== "file") return;
    try {
      const payload = await getFile(entry.path);
      selectedFile.value = payload;
      if (payload.encoding === "text" && fileBaselines[payload.path] == null) {
        fileBaselines[payload.path] = payload.content;
      }
      activeInspector.value = "sandbox";
    } catch (error) {
      workspaceError.value = readableError(error);
    }
  }

  async function selectArtifact(item: ArtifactItem) {
    selectedArtifact.value = item;
    artifactPayload.value = null;
    activeInspector.value = "artifacts";
    if (["markdown", "text", "code"].includes(item.kind)) {
      try {
        artifactPayload.value = await getFile(item.path);
      } catch (error) {
        artifactError.value = readableError(error);
      }
    }
  }

  async function handleFileInput(event: Event) {
    const target = event.target as HTMLInputElement;
    const files = Array.from(target.files ?? []);
    const images = files.filter((file) =>
      ["image/png", "image/jpeg", "image/webp", "image/gif"].includes(file.type),
    );
    pendingImages.value.push(...(await Promise.all(images.map(fileToPendingImage))));
    target.value = "";
  }

  function removePendingImage(id: string) {
    pendingImages.value = pendingImages.value.filter((image) => image.id !== id);
  }

  async function submitMessage() {
    const text = draft.value.trim();
    if (!text && pendingImages.value.length === 0) return;

    streamError.value = null;
    const submittedDraft = draft.value;
    const submittedImages = [...pendingImages.value];

    const contentBlocks: any[] = [];
    if (text) contentBlocks.push({ type: "text", text });
    for (const image of pendingImages.value) {
      contentBlocks.push({
        type: "image_url",
        image_url: { url: image.dataUrl },
      });
    }

    const content =
      contentBlocks.length === 1 && text && pendingImages.value.length === 0
        ? text
        : contentBlocks;

    const optimisticHumanMessage = {
      id: `local-human-${Date.now()}`,
      type: "human",
      role: "human",
      content,
    };

    draft.value = "";
    pendingImages.value = [];
    optimisticMessage.value = optimisticHumanMessage;
    historyMessages.value = null;

    try {
      await prepareThreadForSubmit();
      await stream.submit(
        {
          messages: [{ type: "human", content }],
        },
        {
          threadId: activeThreadId.value,
          streamMode: ["messages-tuple"],
          streamSubgraphs: true,
          multitaskStrategy: "rollback",
          config: { recursion_limit: 10000 },
          optimisticValues(previous: AgentState) {
            return {
              messages: mergeMessages(previous.messages, [optimisticHumanMessage]),
            };
          },
          onError(error: unknown) {
            streamError.value = readableError(error);
          },
        } as any,
      );

      if (activeThreadId.value) {
        upsertSession(
          activeThreadId.value,
          text ? text.slice(0, 40) : "图片任务",
        );
      }
    } catch (error) {
      optimisticMessage.value = null;
      streamError.value = readableError(error);
      if (!draft.value) draft.value = submittedDraft;
      if (pendingImages.value.length === 0) pendingImages.value = submittedImages;
    }
  }

  async function startNewConversation() {
    try {
      await stream.stop();
    } catch (error) {
      streamError.value = readableError(error);
    }
    activeThreadId.value = null;
    await syncStreamThread(null);
    localStorage.removeItem(STORAGE_THREAD);
    savedRunId.value = null;
    localStorage.removeItem(STORAGE_RUN);
    streamError.value = null;
    draft.value = "";
    pendingImages.value = [];
    historyMessages.value = [];
    optimisticMessage.value = null;
    selectedArtifact.value = null;
    artifactPayload.value = null;
    selectedFile.value = null;
    for (const key of Object.keys(fileBaselines)) {
      delete fileBaselines[key];
    }
    await refreshSideData();
  }

  async function selectSession(session: SessionRecord) {
    try {
      await stream.stop();
    } catch {}

    activeThreadId.value = session.id;
    await syncStreamThread(session.id);
    localStorage.setItem(STORAGE_THREAD, session.id);
    upsertSession(session.id);
    optimisticMessage.value = null;
    streamError.value = null;
    historyMessages.value = [];
    await loadThreadMessages(session.id);
  }

  async function stopStream() {
    try {
      await stream.stop();
    } catch (error) {
      streamError.value = readableError(error);
    }
  }

  function startInspectorResize(event: PointerEvent) {
    const startX = event.clientX;
    const startWidth = inspectorWidth.value;
    const target = event.currentTarget as HTMLElement;
    target.setPointerCapture(event.pointerId);

    const onMove = (moveEvent: PointerEvent) => {
      const nextWidth = startWidth - (moveEvent.clientX - startX);
      inspectorWidth.value = Math.min(720, Math.max(300, nextWidth));
    };
    const onUp = () => {
      localStorage.setItem(STORAGE_INSPECTOR_WIDTH, String(inspectorWidth.value));
      target.removeEventListener("pointermove", onMove);
      target.removeEventListener("pointerup", onUp);
      target.removeEventListener("pointercancel", onUp);
    };

    target.addEventListener("pointermove", onMove);
    target.addEventListener("pointerup", onUp);
    target.addEventListener("pointercancel", onUp);
  }

  watch(completedMutatingTools, async (current, previous) => {
    if (!current || current === previous) return;
    await Promise.all([loadArtifacts(), loadWorkspace()]);
  });

  onMounted(async () => {
    await refreshSideData();

    if (activeThreadId.value) {
      await syncStreamThread(activeThreadId.value);
      await loadThreadMessages(activeThreadId.value);
    }
  });

  async function loadThreadMessages(threadId: string) {
    try {
      streamIsThreadLoading.value = true;
      const data = await fetchThreadState(threadId);
      const values = data.values ?? data.checkpoint?.values ?? data.state?.values ?? {};
      const nextMessages = values.messages ?? [];
      historyMessages.value = Array.isArray(nextMessages) ? nextMessages : [];
      resetLiveStreamState(values);
      syncInterruptsFromThreadState(data);
    } catch (error) {
      streamError.value = readableError(error);
    } finally {
      streamIsThreadLoading.value = false;
    }
  }

  return {
    activeThreadId,
    savedRunId,
    sessions,
    stream,
    streamError,
    draft,
    pendingImages,
    inspectorWidth,
    manifest,
    manifestError,
    artifacts,
    artifactError,
    selectedArtifact,
    artifactPayload,
    workspaceEntries,
    workspaceError,
    selectedFile,
    activeInspector,
    collapsed,
    chatMessages,
    todos,
    todoStats,
    subagents,
    artifactDrafts,
    subagentByName,
    toolEvents,
    fileDiff,
    selectedArtifactKind,
    callsForMessage,
    subagentsForMessage,
    artifactDraftsForMessage,
    syncCollapsed,
    refreshSideData,
    loadArtifacts,
    loadWorkspace,
    openWorkspaceFile,
    selectArtifact,
    handleFileInput,
    removePendingImage,
    submitMessage,
    startNewConversation,
    selectSession,
    stopStream,
    startInspectorResize,
  };
}
