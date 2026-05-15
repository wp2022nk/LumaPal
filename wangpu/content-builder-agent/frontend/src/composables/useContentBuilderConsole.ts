import { computed, onMounted, reactive, ref, watch } from "vue";
import { fetchAgent, getArtifacts, getFile, getManifest, getTree } from "../api";
import type {
  ActiveInspector,
  AgentManifest,
  AgentState,
  ApprovalDecision,
  ApprovalMode,
  ArtifactItem,
  FilePayload,
  InterruptRequest,
  PendingImage,
  SessionRecord,
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

const ASSISTANT_ID = "content_writer";
const STORAGE_SESSIONS = "content-builder:sessions";
const STORAGE_THREAD = "content-builder:active-thread";
const STORAGE_RUN = "content-builder:active-run";
const STORAGE_INSPECTOR_WIDTH = "content-builder:inspector-width";
const DEFAULT_SESSION_TITLE = "新对话";

function explicitMessageId(message: any): string | null {
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

function messageKeyForMerge(message: any): string {
  return explicitMessageId(message) ?? fallbackMessageKey(message);
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

function mergeMessages(current: unknown[] | undefined, incoming: unknown[]): unknown[] {
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

function collectStatePatch(value: unknown, patch: AgentState = {}): AgentState {
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

  for (const [key, childValue] of Object.entries(objectValue)) {
    if (key === "messages" || key === "todos") continue;
    collectStatePatch(childValue, patch);
  }

  return patch;
}

type RunStreamEvent = {
  event: string;
  data: unknown;
};

function parseSseEvent(frame: string): RunStreamEvent | null {
  const lines = frame.split(/\r?\n/);
  const event = lines
    .find((line) => line.startsWith("event:"))
    ?.slice("event:".length)
    .trim();
  const dataText = lines
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice("data:".length).trimStart())
    .join("\n");

  if (!event && !dataText) return null;

  let data: unknown = dataText;
  if (dataText) {
    try {
      data = JSON.parse(dataText);
    } catch {
      data = dataText;
    }
  }

  return { event: event ?? "message", data };
}

async function* iterateSseEvents(
  response: Response,
): AsyncGenerator<RunStreamEvent> {
  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      while (true) {
        const crlfIndex = buffer.indexOf("\r\n\r\n");
        const lfIndex = buffer.indexOf("\n\n");
        let frameEnd = -1;
        let separatorLength = 2;

        if (crlfIndex >= 0 && (lfIndex < 0 || crlfIndex < lfIndex)) {
          frameEnd = crlfIndex;
          separatorLength = 4;
        } else if (lfIndex >= 0) {
          frameEnd = lfIndex;
        } else {
          break;
        }

        const frame = buffer.slice(0, frameEnd);
        buffer = buffer.slice(frameEnd + separatorLength);

        const parsed = parseSseEvent(frame);
        if (parsed) yield parsed;
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {}
  }
}

function normalizeStreamingChunk(message: any): any {
  const role = roleOf(message);
  if (role === "AIMessageChunk") {
    return { ...message, type: "ai", role: "ai" };
  }
  return message;
}

function shouldFinishFromValues(values: AgentState): boolean {
  const messages = Array.isArray(values.messages) ? values.messages : [];
  const lastMessage = messages.at(-1) as any;
  const role = roleOf(lastMessage);

  if (role !== "ai" && role !== "assistant") return false;
  if (parseToolCalls(lastMessage).length > 0) return false;

  return Boolean(messageText(lastMessage).trim());
}

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

  const approvalMode = ref<ApprovalMode>("review");
  const approvalArgsText = ref("{}");
  const rejectionReason = ref("");
  const responseMessage = ref("");

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
  const streamingContentById = new Map<string, string>();
  let activeRunAbortController: AbortController | null = null;

  function resetLiveStreamState(values: AgentState = {}) {
    streamValues.value = values;
    streamMessagesState.value = Array.isArray(values.messages)
      ? (values.messages as any[])
      : [];
    streamToolCalls.value = [];
    streamInterrupts.value = [];
    streamSdkError.value = null;
    streamingContentById.clear();
  }

  async function readResponseError(response: Response): Promise<string> {
    try {
      return await response.text();
    } catch {
      return `${response.status} ${response.statusText}`;
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

  function applyStatePatch(patch: AgentState) {
    const nextValues: AgentState = { ...streamValues.value, ...patch };

    if (Array.isArray(patch.messages)) {
      streamMessagesState.value = mergeMessages(
        streamMessagesState.value,
        patch.messages,
      ) as any[];
      nextValues.messages = streamMessagesState.value;
    }

    if (Array.isArray((patch as any).__interrupt__)) {
      streamInterrupts.value = (patch as any).__interrupt__;
    }

    streamValues.value = nextValues;
  }

  function applyStreamingMessageEvent(data: unknown) {
    if (!Array.isArray(data) || !data[0]) return;

    const chunk = normalizeStreamingChunk(data[0]);
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
      type: roleOf(chunk) === "AIMessageChunk" ? "ai" : roleOf(chunk) || "ai",
      role: roleOf(chunk) === "AIMessageChunk" ? "ai" : roleOf(chunk) || "ai",
      content: nextText || messageText(chunk),
    };

    applyStatePatch({ messages: [message] });

    if (chunk?.chunk_position === "last") {
      streamingContentById.delete(id);
    }
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

        if (event.event === "metadata" && event.data && typeof event.data === "object") {
          const runId = (event.data as any).run_id;
          if (typeof runId === "string") {
            savedRunId.value = runId;
            localStorage.setItem(STORAGE_RUN, runId);
          }
          continue;
        }

        if (event.event === "messages") {
          applyStreamingMessageEvent(event.data);
          continue;
        }

        if (event.event === "values") {
          const patch = collectStatePatch(event.data);
          applyStatePatch(patch);
          if (shouldFinishFromValues(patch)) break;
          continue;
        }

        if (event.event === "updates" || event.event === "tasks") {
          applyStatePatch(collectStatePatch(event.data));
          continue;
        }

        if (event.event === "error") {
          throw new Error(readableError(event.data));
        }

        if (event.event === "end") break;
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
    return map?.values ? Array.from(map.values()) : [];
  });

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

  const interruptRequest = computed<InterruptRequest | null>(() => {
    const raw = stream.interrupt.value as any;
    const value = raw?.value ?? raw;
    if (!value) return null;
    const actions = value.actionRequests ?? value.action_requests ?? [];
    const reviews = value.reviewConfigs ?? value.review_configs ?? [];
    const action = Array.isArray(actions) ? actions[0] : actions;
    const review = Array.isArray(reviews) ? reviews[0] : reviews;
    if (!action) return null;

    return {
      raw,
      action: {
        action: String(action.action ?? action.name ?? "tool"),
        args: normalizeObject(action.args),
        description: action.description,
      },
      review: {
        allowedDecisions:
          review?.allowedDecisions ??
          review?.allowed_decisions ??
          ["approve", "reject", "edit"],
      },
    };
  });

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

  async function sendApproval(decision: ApprovalDecision) {
    try {
      let resume: Record<string, unknown> = { decision };
      if (decision === "reject") {
        resume = {
          decision,
          reason: rejectionReason.value || "用户拒绝执行该操作",
        };
      }
      if (decision === "respond") {
        resume = { decision, message: responseMessage.value };
      }
      if (decision === "edit") {
        resume = { decision, args: JSON.parse(approvalArgsText.value || "{}") };
      }
      await stream.submit(null, {
        command: { resume },
        streamSubgraphs: true,
      } as any);
      approvalMode.value = "review";
      rejectionReason.value = "";
      responseMessage.value = "";
    } catch (error) {
      streamError.value = readableError(error);
    }
  }

  watch(completedMutatingTools, async (current, previous) => {
    if (!current || current === previous) return;
    await Promise.all([loadArtifacts(), loadWorkspace()]);
  });

  watch(interruptRequest, (request) => {
    if (!request) return;
    approvalMode.value = "review";
    approvalArgsText.value = JSON.stringify(request.action.args ?? {}, null, 2);
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
      const res = await fetchAgent(`/threads/${threadId}/state`);
      if (!res.ok) throw new Error(`读取会话失败: ${res.status} ${res.statusText}`);

      const data = await res.json();
      const values = data.values ?? data.checkpoint?.values ?? data.state?.values ?? {};
      const nextMessages = values.messages ?? [];
      historyMessages.value = Array.isArray(nextMessages) ? nextMessages : [];
      resetLiveStreamState(values);
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
    approvalMode,
    approvalArgsText,
    rejectionReason,
    responseMessage,
    chatMessages,
    todos,
    todoStats,
    subagents,
    subagentByName,
    toolEvents,
    interruptRequest,
    fileDiff,
    selectedArtifactKind,
    callsForMessage,
    subagentsForMessage,
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
    sendApproval,
  };
}
