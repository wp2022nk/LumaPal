<script setup lang="ts">
import { useStream } from "@langchain/vue";
import DOMPurify from "dompurify";
import {
  Bot,
  BookOpen,
  Braces,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock3,
  Code2,
  File,
  FileImage,
  FileText,
  Folder,
  Image as ImageIcon,
  ListChecks,
  Loader2,
  PanelRight,
  Play,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  Sparkles,
  Terminal,
  Upload,
  Wrench,
  X,
  XCircle,
} from "lucide-vue-next";
import MarkdownIt from "markdown-it";
import { computed, nextTick, onMounted, reactive, ref, watch } from "vue";
import {
  AGENT_URL,
  assetUrl,
  getArtifacts,
  getFile,
  getManifest,
  getTree,
} from "./api";
import type {
  AgentManifest,
  ArtifactItem,
  FilePayload,
  PendingImage,
  SessionRecord,
  ToolManifest,
  WorkspaceEntry,
} from "./types";
import {
  fileToPendingImage,
  humanSize,
  imageUrlsFromContent as baseImageUrlsFromContent,
  messageId as baseMessageId,
  roleOf as baseRoleOf,
  shortDate,
  simpleLineDiff,
  textFromContent as baseTextFromContent,
} from "./utils";

interface TodoItem {
  content?: string;
  title?: string;
  status?: "pending" | "in_progress" | "completed" | string;
}

interface AgentState {
  messages?: unknown[];
  todos?: TodoItem[];
  [key: string]: unknown;
}

interface ToolCallView {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

interface ToolEventView {
  id: string;
  call: ToolCallView;
  result?: {
    content: string;
    status?: string;
  };
  tool?: ToolManifest;
  messageId?: string;
  running?: boolean;
}

interface InterruptAction {
  action: string;
  args: Record<string, unknown>;
  description?: string;
}

interface InterruptReview {
  allowedDecisions: string[];
}

const ASSISTANT_ID = "content_writer";
const STORAGE_SESSIONS = "content-builder:sessions";
const STORAGE_THREAD = "content-builder:active-thread";
const STORAGE_RUN = "content-builder:active-run";

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

const activeThreadId = ref<string | null>(localStorage.getItem(STORAGE_THREAD));
const savedRunId = ref<string | null>(localStorage.getItem(STORAGE_RUN));
const localMessages = ref<any[]>([]);
const draft = ref("");
const pendingImages = ref<PendingImage[]>([]);
const fileInput = ref<HTMLInputElement | null>(null);
const messagePane = ref<HTMLElement | null>(null);
const messagesEnd = ref<HTMLElement | null>(null);
const streamError = ref<string | null>(null);
const inspectorWidth = ref(
  Number(localStorage.getItem("content-builder:inspector-width")) || 430,
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
const activeInspector = ref<"plan" | "flow" | "artifacts" | "sandbox">("plan");
const collapsed = reactive<Record<string, boolean>>({
  manifestTools: false,
  manifestSkills: false,
  manifestSubagents: false,
  plan: false,
  flow: false,
  artifacts: false,
  sandbox: false,
});

const approvalMode = ref<"review" | "edit" | "reject" | "respond">("review");
const approvalArgsText = ref("{}");
const rejectionReason = ref("");
const responseMessage = ref("");

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

const sessions = ref<SessionRecord[]>(loadSessions());

function saveSessions() {
  localStorage.setItem(STORAGE_SESSIONS, JSON.stringify(sessions.value));
}

function upsertSession(id: string, title?: string) {
  const now = new Date().toISOString();
  const existing = sessions.value.find((session) => session.id === id);
  if (existing) {
    existing.updatedAt = now;
    if (title && existing.title.startsWith("新对话")) existing.title = title;
  } else {
    sessions.value.unshift({
      id,
      title: title || `新对话 ${sessions.value.length + 1}`,
      createdAt: now,
      updatedAt: now,
    });
  }
  saveSessions();
}

const stream = useStream<AgentState>({
  apiUrl: AGENT_URL,
  assistantId: ASSISTANT_ID,
  threadId: activeThreadId,
  filterSubagentMessages: true,
  onThreadId(threadId: string) {
    activeThreadId.value = threadId;
    localStorage.setItem(STORAGE_THREAD, threadId);
    upsertSession(threadId);
  },
  onCreated(run: { run_id: string; thread_id: string }) {
    savedRunId.value = run.run_id;
    localStorage.setItem(STORAGE_RUN, run.run_id);
    activeThreadId.value = run.thread_id;
    localStorage.setItem(STORAGE_THREAD, run.thread_id);
    upsertSession(run.thread_id);
  },
  onError(error: unknown) {
    streamError.value = readableError(error);
  },
} as any);

function extractStreamMessages(): any[] {
  const direct = stream.messages.value;
  if (Array.isArray(direct) && direct.length > 0) return direct;

  const values = stream.values.value as AgentState;
  if (Array.isArray(values?.messages) && values.messages.length > 0) {
    return values.messages;
  }

  return [];
}

function syncMessagesFromStream() {
  const nextMessages = extractStreamMessages();
  if (nextMessages.length > 0) {
    localMessages.value = [...nextMessages];
  }
}

watch(
  () => [stream.messages.value, (stream.values.value as AgentState)?.messages],
  () => {
    syncMessagesFromStream();
  },
  { deep: true, immediate: true },
);

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

const messages = computed(() => localMessages.value);
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
  const completed = todos.value.filter(
    (todo) => todo.status === "completed",
  ).length;
  const active = todos.value.filter(
    (todo) => todo.status === "in_progress",
  ).length;
  return {
    total,
    completed,
    active,
    percent: total ? Math.round((completed / total) * 100) : 0,
  };
});

const subagents = computed(() =>
  Array.from(stream.subagents.value?.values?.() ?? []),
);

const toolResultById = computed(() => {
  const map = new Map<string, { content: string; status?: string }>();
  for (const message of messages.value) {
    if (roleOf(message) !== "tool") continue;
    const toolCallId = String(
      (message as any).tool_call_id ??
        (message as any).toolCallId ??
        (message as any).additional_kwargs?.tool_call_id ??
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
  for (const assembled of stream.toolCalls.value ?? []) {
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

const interruptRequest = computed(() => {
  const raw = stream.interrupt.value as any;
  const value = raw?.value ?? raw;
  if (!value) return null;
  const actions = value.actionRequests ?? value.action_requests ?? [];
  const reviews = value.reviewConfigs ?? value.review_configs ?? [];
  const action = Array.isArray(actions) ? actions[0] : actions;
  const review = Array.isArray(reviews) ? reviews[0] : reviews;
  if (!action) return null;
  const normalizedAction: InterruptAction = {
    action: String(action.action ?? action.name ?? "tool"),
    args: normalizeObject(action.args),
    description: action.description,
  };
  const normalizedReview: InterruptReview = {
    allowedDecisions: review?.allowedDecisions ??
      review?.allowed_decisions ?? ["approve", "reject", "edit"],
  };
  return {
    raw,
    action: normalizedAction,
    review: normalizedReview,
  };
});

const fileDiff = computed(() => {
  if (!selectedFile.value || selectedFile.value.encoding !== "text") return [];
  const baseline =
    fileBaselines[selectedFile.value.path] ?? selectedFile.value.content;
  return simpleLineDiff(baseline, selectedFile.value.content);
});

const selectedArtifactKind = computed(
  () => selectedArtifact.value?.kind ?? "binary",
);

function readableError(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  try {
    return JSON.stringify(error);
  } catch {
    return "未知错误";
  }
}

function renderMarkdown(value: string): string {
  return DOMPurify.sanitize(markdown.render(value || ""));
}

function roleOf(message: any): string {
  if (!message) return "";

  if (typeof message.role === "string") return message.role;
  if (typeof message.type === "string") return message.type;

  if (typeof message._getType === "function") {
    return message._getType();
  }

  if (message.constructor?.name === "HumanMessage") return "human";
  if (message.constructor?.name === "AIMessage") return "ai";
  if (message.constructor?.name === "ToolMessage") return "tool";

  if (typeof message.kwargs?.type === "string") return message.kwargs.type;
  if (typeof message.lc_kwargs?.type === "string")
    return message.lc_kwargs.type;

  try {
    return baseRoleOf(message);
  } catch {
    return "";
  }
}

function messageId(message: any, index?: number): string {
  const id =
    message?.id ??
    message?.message_id ??
    message?.uuid ??
    message?.lc_kwargs?.id ??
    message?.kwargs?.id;

  if (id != null && id !== "") return String(id);

  try {
    const baseId = baseMessageId(message, index);
    if (baseId) return String(baseId);
  } catch {}

  return `message-${index ?? Date.now()}`;
}

function textFromContent(content: any): string {
  if (typeof content === "string") return content;

  if (Array.isArray(content)) {
    return content
      .map((block) => {
        if (typeof block === "string") return block;
        if (block?.type === "text") return block.text ?? "";
        if (typeof block?.text === "string") return block.text;
        return "";
      })
      .filter(Boolean)
      .join("\n");
  }

  if (content && typeof content === "object") {
    if (typeof content.text === "string") return content.text;
    if (typeof content.content === "string") return content.content;
  }

  try {
    return baseTextFromContent(content);
  } catch {
    return "";
  }
}

function imageUrlsFromContent(content: any): string[] {
  const urls: string[] = [];

  if (Array.isArray(content)) {
    for (const block of content) {
      if (typeof block?.image_url === "string") urls.push(block.image_url);
      if (typeof block?.image_url?.url === "string")
        urls.push(block.image_url.url);
      if (typeof block?.url === "string" && block?.type === "image_url") {
        urls.push(block.url);
      }
    }
  }

  try {
    urls.push(...baseImageUrlsFromContent(content));
  } catch {}

  return Array.from(new Set(urls.filter(Boolean)));
}

function messageText(message: any): string {
  return textFromContent(message?.content ?? message?.text ?? "");
}

function messageImages(message: any): string[] {
  return imageUrlsFromContent(message?.content);
}

function parseJsonMaybe(value: unknown): unknown {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function normalizeObject(value: unknown): Record<string, unknown> {
  const parsed = parseJsonMaybe(value);
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed))
    return parsed as Record<string, unknown>;
  if (parsed == null || parsed === "") return {};
  return { value: parsed };
}

function parseToolCalls(message: any): ToolCallView[] {
  const direct = Array.isArray(message?.tool_calls) ? message.tool_calls : [];
  const openAi = Array.isArray(message?.additional_kwargs?.tool_calls)
    ? message.additional_kwargs.tool_calls
    : [];
  const calls = direct.length ? direct : openAi;
  return calls.map((call: any, index: number) => {
    const name = call.name ?? call.function?.name ?? `tool_${index + 1}`;
    const args = call.args ?? call.function?.arguments ?? {};
    return {
      id: String(
        call.id ?? call.call_id ?? `${messageId(message)}:${name}:${index}`,
      ),
      name: String(name),
      args: normalizeObject(args),
    };
  });
}

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
  return subagents.value.filter((subagent: any) =>
    ids.has(String(subagent.id)),
  );
}

function schemaFields(tool: ToolManifest | undefined): Array<{
  name: string;
  type: string;
  required: boolean;
  description: string;
}> {
  const schema = tool?.schema as any;
  const properties =
    schema?.properties && typeof schema.properties === "object"
      ? schema.properties
      : {};
  const required = new Set(
    Array.isArray(schema?.required) ? schema.required : [],
  );
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

function summarizeResult(content = ""): string {
  const clean = content.replace(/\s+/g, " ").trim();
  if (!clean) return "已完成";
  return clean.length > 180 ? `${clean.slice(0, 180)}...` : clean;
}

function toolStatus(event: ToolEventView): string {
  if (event.result?.status === "error") return "error";
  return event.running ? "running" : "finished";
}

function todoLabel(todo: TodoItem): string {
  return String(todo.content ?? todo.title ?? "未命名任务");
}

function statusText(status?: string): string {
  if (status === "completed" || status === "complete" || status === "finished")
    return "完成";
  if (status === "in_progress" || status === "running") return "执行中";
  if (status === "error") return "失败";
  return "等待";
}

function shortId(value: string | null | undefined): string {
  if (!value) return "未创建";
  return value.length > 12
    ? `${value.slice(0, 8)}...${value.slice(-4)}`
    : value;
}

function pathDepth(path: string): number {
  if (!path || path === ".") return 0;
  return Math.max(0, path.split(/[\\/]/).length - 1);
}

function fileIcon(entry: WorkspaceEntry | ArtifactItem) {
  if (entry.type === "directory") return Folder;
  if (entry.kind === "image") return FileImage;
  if (entry.kind === "markdown" || entry.kind === "text") return FileText;
  if (entry.kind === "code") return Code2;
  return File;
}

function syncCollapsed(key: string, event: Event) {
  collapsed[key] = !(event.target as HTMLDetailsElement).open;
}

function allowedDecision(decision: string): boolean {
  const allowed = interruptRequest.value?.review.allowedDecisions ?? [];
  return allowed.includes(decision);
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
        artifacts.value.find(
          (item) => item.path === selectedArtifact.value?.path,
        ) ?? selectedArtifact.value;
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
  pendingImages.value.push(
    ...(await Promise.all(images.map(fileToPendingImage))),
  );
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

  const optimisticThread = activeThreadId.value;
  const optimisticHumanMessage = {
    id: `local-human-${Date.now()}`,
    type: "human",
    role: "human",
    content,
  };

  draft.value = "";
  pendingImages.value = [];

  // 先写入本地消息，确保用户点击发送后立刻在聊天区可见。
  localMessages.value = [...localMessages.value, optimisticHumanMessage];

  await nextTick();
  messagesEnd.value?.scrollIntoView({ behavior: "smooth", block: "end" });

  try {
    await stream.submit(
      {
        messages: [{ type: "human", content }],
      },
      {
        streamSubgraphs: true,
        multitaskStrategy: "rollback",
        config: { recursion_limit: 10000 },
        onError(error: unknown) {
          streamError.value = readableError(error);
        },
      } as any,
    );

    syncMessagesFromStream();

    if (optimisticThread || activeThreadId.value) {
      upsertSession(
        activeThreadId.value ?? optimisticThread!,
        text ? text.slice(0, 40) : "图片任务",
      );
    }
  } catch (error) {
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
  localStorage.removeItem(STORAGE_THREAD);
  savedRunId.value = null;
  localStorage.removeItem(STORAGE_RUN);
  localMessages.value = [];
  streamError.value = null;
  draft.value = "";
  pendingImages.value = [];
  selectedArtifact.value = null;
  artifactPayload.value = null;
  selectedFile.value = null;
  for (const key of Object.keys(fileBaselines)) {
    delete fileBaselines[key];
  }
  await refreshSideData();
}

async function loadThreadMessages(threadId: string) {
  try {
    streamError.value = null;
    const res = await fetch(`${AGENT_URL}/threads/${threadId}/state`);

    if (!res.ok) {
      throw new Error(`读取会话失败：${res.status} ${res.statusText}`);
    }

    const data = await res.json();
    const values =
      data.values ?? data.checkpoint?.values ?? data.state?.values ?? {};
    const nextMessages = values.messages ?? [];

    localMessages.value = Array.isArray(nextMessages) ? [...nextMessages] : [];
  } catch (error) {
    streamError.value = readableError(error);
  }
}

async function selectSession(session: SessionRecord) {
  try {
    await stream.stop();
  } catch {}

  activeThreadId.value = session.id;
  localStorage.setItem(STORAGE_THREAD, session.id);
  upsertSession(session.id);

  localMessages.value = [];
  await nextTick();
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
    localStorage.setItem(
      "content-builder:inspector-width",
      String(inspectorWidth.value),
    );
    target.removeEventListener("pointermove", onMove);
    target.removeEventListener("pointerup", onUp);
    target.removeEventListener("pointercancel", onUp);
  };

  target.addEventListener("pointermove", onMove);
  target.addEventListener("pointerup", onUp);
  target.addEventListener("pointercancel", onUp);
}

function shouldStickToBottom(): boolean {
  const pane = messagePane.value;
  if (!pane) return true;
  return pane.scrollHeight - pane.scrollTop - pane.clientHeight < 140;
}

async function sendApproval(
  decision: "approve" | "reject" | "edit" | "respond",
) {
  try {
    let resume: Record<string, unknown> = { decision };
    if (decision === "reject")
      resume = {
        decision,
        reason: rejectionReason.value || "用户拒绝执行该操作",
      };
    if (decision === "respond")
      resume = { decision, message: responseMessage.value };
    if (decision === "edit")
      resume = { decision, args: JSON.parse(approvalArgsText.value || "{}") };
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

watch(
  () => stream.threadId.value,
  (threadId) => {
    if (!threadId) return;
    activeThreadId.value = threadId;
    localStorage.setItem(STORAGE_THREAD, threadId);
    upsertSession(threadId);
  },
);

watch(
  chatMessages,
  async () => {
    const stickToBottom = shouldStickToBottom();
    await nextTick();
    if (stickToBottom) {
      messagesEnd.value?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  },
  { deep: true },
);

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
    await loadThreadMessages(activeThreadId.value);
  }
});
</script>

<template>
  <div
    class="app-shell"
    :style="{ '--inspector-width': `${inspectorWidth}px` }"
  >
    <aside class="sidebar">
      <header class="brand-block">
        <div class="brand-mark">
          <Sparkles :size="20" />
        </div>
        <div>
          <h1>Content Builder</h1>
          <p>Deep Agents Vue Console</p>
        </div>
      </header>

      <button
        class="primary-action"
        type="button"
        @click="startNewConversation"
      >
        <Plus :size="17" />
        新建对话
      </button>

      <section class="side-section">
        <div class="section-title">
          <span>会话</span>
          <span>{{ sessions.length }}</span>
        </div>
        <div class="session-list">
          <button
            v-for="session in sessions"
            :key="session.id"
            class="session-item"
            :class="{ active: session.id === activeThreadId }"
            type="button"
            @click="selectSession(session)"
          >
            <span>{{ session.title }}</span>
            <small
              >{{ shortDate(session.updatedAt) }} ·
              {{ shortId(session.id) }}</small
            >
          </button>
          <p v-if="sessions.length === 0" class="muted">
            发送第一条消息后会自动创建 thread。
          </p>
        </div>
      </section>

      <section class="side-section manifest-card">
        <div class="section-title">
          <span>Manifest</span>
          <button
            class="icon-button"
            type="button"
            title="刷新 manifest"
            @click="refreshSideData"
          >
            <RefreshCw :size="15" />
          </button>
        </div>
        <p v-if="manifestError" class="error-text">{{ manifestError }}</p>
        <template v-else-if="manifest">
          <dl class="manifest-meta">
            <div>
              <dt>Assistant</dt>
              <dd>{{ manifest.agent.assistant_id }}</dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>{{ manifest.agent.model }}</dd>
            </div>
            <div>
              <dt>Sandbox</dt>
              <dd>
                {{ manifest.backend.sandbox_python || manifest.backend.type }}
              </dd>
            </div>
          </dl>

          <details
            :open="!collapsed.manifestTools"
            @toggle="syncCollapsed('manifestTools', $event)"
          >
            <summary>
              <Wrench :size="15" /> 工具 {{ manifest.tools.length }}
            </summary>
            <div class="chip-list">
              <span
                v-for="tool in manifest.tools"
                :key="tool.name"
                class="chip"
                :class="{ warn: tool.requires_approval }"
              >
                {{ tool.name }}
              </span>
            </div>
          </details>

          <details
            :open="!collapsed.manifestSubagents"
            @toggle="syncCollapsed('manifestSubagents', $event)"
          >
            <summary>
              <Bot :size="15" /> 子智能体 {{ manifest.subagents.length }}
            </summary>
            <div class="stack-list">
              <div
                v-for="subagent in manifest.subagents"
                :key="subagent.name"
                class="mini-row"
              >
                <strong>{{ subagent.name }}</strong>
                <span>{{ subagent.description }}</span>
              </div>
            </div>
          </details>

          <details
            :open="!collapsed.manifestSkills"
            @toggle="syncCollapsed('manifestSkills', $event)"
          >
            <summary>
              <BookOpen :size="15" /> Skills {{ manifest.skills.length }}
            </summary>
            <div class="stack-list">
              <div
                v-for="skill in manifest.skills"
                :key="skill.path"
                class="mini-row"
              >
                <strong>{{ skill.title || skill.name }}</strong>
                <span>{{ skill.description || skill.path }}</span>
              </div>
            </div>
          </details>
        </template>
        <p v-else class="muted">正在读取后端 manifest...</p>
      </section>
    </aside>

    <main class="conversation">
      <header class="topbar">
        <div>
          <div class="eyebrow">LangGraph Agent Server</div>
          <h2>{{ manifest?.agent.name || "content_writer" }}</h2>
        </div>
        <div class="runtime-strip">
          <span class="status-pill" :class="{ live: stream.isLoading.value }">
            <span class="status-dot" />
            {{ stream.isLoading.value ? "流式执行中" : "待命" }}
          </span>
          <span class="status-pill"
            >Thread {{ shortId(stream.threadId.value || activeThreadId) }}</span
          >
          <button
            v-if="stream.isLoading.value"
            class="ghost-button"
            type="button"
            @click="stopStream"
          >
            停止接收
          </button>
        </div>
      </header>

      <section ref="messagePane" class="message-pane">
        <div v-if="chatMessages.length === 0" class="empty-state">
          <div class="empty-mark"><Bot :size="30" /></div>
          <h3>把任务、素材或草稿丢进来</h3>
          <p>适合推进博客、故事、研究分析、图像素材和代码执行相关任务。</p>
        </div>

        <article
          v-for="(message, index) in chatMessages"
          :key="messageId(message, index)"
          class="message"
          :class="roleOf(message)"
        >
          <div class="avatar">
            <Bot
              v-if="['ai', 'assistant'].includes(roleOf(message))"
              :size="18"
            />
            <span v-else>你</span>
          </div>
          <div class="message-body">
            <div class="message-meta">
              <strong>{{
                ["ai", "assistant"].includes(roleOf(message))
                  ? "智能体"
                  : "用户"
              }}</strong>
              <span>{{ roleOf(message) }}</span>
            </div>
            <div
              v-if="messageText(message)"
              class="markdown-body"
              v-html="renderMarkdown(messageText(message))"
            />
            <div v-if="messageImages(message).length" class="image-grid">
              <img
                v-for="image in messageImages(message)"
                :key="image"
                :src="image"
                alt="uploaded image"
              />
            </div>

            <div v-if="callsForMessage(message).length" class="tool-card-list">
              <details
                v-for="event in callsForMessage(message)"
                :key="event.id"
                class="tool-card"
                open
              >
                <summary>
                  <span class="tool-title">
                    <Loader2
                      v-if="toolStatus(event) === 'running'"
                      class="spin"
                      :size="16"
                    />
                    <XCircle
                      v-else-if="toolStatus(event) === 'error'"
                      :size="16"
                    />
                    <CheckCircle2 v-else :size="16" />
                    {{ event.call.name }}
                  </span>
                  <span class="tool-state">{{
                    statusText(toolStatus(event))
                  }}</span>
                </summary>
                <p v-if="event.tool?.description" class="tool-description">
                  {{ event.tool.description }}
                </p>
                <div
                  v-if="schemaFields(event.tool).length"
                  class="schema-table"
                >
                  <div
                    v-for="field in schemaFields(event.tool)"
                    :key="field.name"
                  >
                    <strong>{{ field.name }}</strong>
                    <span>{{ field.type }}</span>
                    <em>{{ field.required ? "必填" : "可选" }}</em>
                  </div>
                </div>
                <pre>{{ JSON.stringify(event.call.args, null, 2) }}</pre>
                <p v-if="event.result" class="result-preview">
                  {{ summarizeResult(event.result.content) }}
                </p>
              </details>
            </div>

            <div
              v-if="subagentsForMessage(message).length"
              class="subagent-card-list"
            >
              <details
                v-for="subagent in subagentsForMessage(message)"
                :key="subagent.id"
                class="subagent-card"
                open
              >
                <summary>
                  <span>
                    <Bot :size="16" />
                    {{
                      subagent.name ||
                      subagent.toolCall?.args?.subagent_type ||
                      subagent.id
                    }}
                  </span>
                  <span>{{ statusText(subagent.status) }}</span>
                </summary>
                <p>
                  {{
                    subagent.taskInput ||
                    subagent.toolCall?.args?.description ||
                    subagentByName.get(subagent.name)
                  }}
                </p>
                <pre v-if="subagent.output || subagent.result">{{
                  typeof (subagent.output || subagent.result) === "string"
                    ? subagent.output || subagent.result
                    : JSON.stringify(
                        subagent.output || subagent.result,
                        null,
                        2,
                      )
                }}</pre>
              </details>
            </div>
          </div>
        </article>

        <section v-if="interruptRequest" class="approval-card">
          <header>
            <ShieldCheck :size="20" />
            <div>
              <h3>需要人工审批</h3>
              <p>
                {{
                  interruptRequest.action.description ||
                  `智能体请求执行 ${interruptRequest.action.action}`
                }}
              </p>
            </div>
          </header>
          <pre>{{ JSON.stringify(interruptRequest.action.args, null, 2) }}</pre>
          <div class="approval-tabs">
            <button
              v-if="allowedDecision('approve')"
              class="approve"
              type="button"
              @click="sendApproval('approve')"
            >
              批准
            </button>
            <button
              v-if="allowedDecision('edit')"
              type="button"
              @click="
                approvalMode = approvalMode === 'edit' ? 'review' : 'edit'
              "
            >
              编辑参数
            </button>
            <button
              v-if="allowedDecision('reject')"
              type="button"
              @click="
                approvalMode = approvalMode === 'reject' ? 'review' : 'reject'
              "
            >
              拒绝
            </button>
            <button
              v-if="allowedDecision('respond')"
              type="button"
              @click="
                approvalMode = approvalMode === 'respond' ? 'review' : 'respond'
              "
            >
              直接回复
            </button>
          </div>
          <div v-if="approvalMode === 'edit'" class="approval-editor">
            <textarea v-model="approvalArgsText" spellcheck="false" />
            <button type="button" @click="sendApproval('edit')">
              提交编辑后参数
            </button>
          </div>
          <div v-if="approvalMode === 'reject'" class="approval-editor">
            <textarea v-model="rejectionReason" placeholder="拒绝原因" />
            <button type="button" @click="sendApproval('reject')">
              提交拒绝
            </button>
          </div>
          <div v-if="approvalMode === 'respond'" class="approval-editor">
            <textarea
              v-model="responseMessage"
              placeholder="给工具返回的人工回复"
            />
            <button type="button" @click="sendApproval('respond')">
              发送回复
            </button>
          </div>
        </section>
        <div ref="messagesEnd" />
      </section>

      <footer class="composer">
        <div v-if="streamError" class="error-banner">{{ streamError }}</div>
        <div v-if="pendingImages.length" class="pending-images">
          <div
            v-for="image in pendingImages"
            :key="image.id"
            class="pending-image"
          >
            <img :src="image.dataUrl" :alt="image.name" />
            <span>{{ image.name }} · {{ humanSize(image.size) }}</span>
            <button type="button" @click="removePendingImage(image.id)">
              <X :size="14" />
            </button>
          </div>
        </div>
        <form class="composer-row" @submit.prevent="submitMessage">
          <input
            ref="fileInput"
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            multiple
            hidden
            @change="handleFileInput"
          />
          <button
            class="icon-button"
            type="button"
            title="上传图片"
            @click="fileInput?.click()"
          >
            <Upload :size="18" />
          </button>
          <textarea
            v-model="draft"
            rows="1"
            placeholder="输入任务，或上传图片一起发送..."
            @keydown.enter.exact.prevent="submitMessage"
          />
          <button
            class="send-button"
            type="submit"
            :disabled="
              stream.isLoading.value &&
              !draft.trim() &&
              pendingImages.length === 0
            "
          >
            <Send :size="18" />
          </button>
        </form>
      </footer>
    </main>

    <div
      class="inspector-resizer"
      role="separator"
      aria-orientation="vertical"
      title="拖动调整右侧面板宽度"
      @pointerdown="startInspectorResize"
    />

    <aside class="inspector">
      <nav class="inspector-tabs" aria-label="Inspector panels">
        <button
          :class="{ active: activeInspector === 'plan' }"
          type="button"
          title="任务规划"
          @click="activeInspector = 'plan'"
        >
          <ListChecks :size="17" />
        </button>
        <button
          :class="{ active: activeInspector === 'flow' }"
          type="button"
          title="执行流"
          @click="activeInspector = 'flow'"
        >
          <PanelRight :size="17" />
        </button>
        <button
          :class="{ active: activeInspector === 'artifacts' }"
          type="button"
          title="中间产物"
          @click="activeInspector = 'artifacts'"
        >
          <ImageIcon :size="17" />
        </button>
        <button
          :class="{ active: activeInspector === 'sandbox' }"
          type="button"
          title="代码沙盒"
          @click="activeInspector = 'sandbox'"
        >
          <Terminal :size="17" />
        </button>
      </nav>

      <section v-show="activeInspector === 'plan'" class="panel">
        <header class="panel-header">
          <div>
            <h3>任务规划</h3>
            <p>{{ todoStats.completed }}/{{ todoStats.total }} 完成</p>
          </div>
          <button
            class="icon-button"
            type="button"
            @click="collapsed.plan = !collapsed.plan"
          >
            <ChevronRight v-if="collapsed.plan" :size="16" />
            <ChevronDown v-else :size="16" />
          </button>
        </header>
        <div v-show="!collapsed.plan" class="panel-content">
          <div class="progress-track">
            <span :style="{ width: `${todoStats.percent}%` }" />
          </div>
          <ol v-if="todos.length" class="todo-list">
            <li
              v-for="(todo, index) in todos"
              :key="`${todoLabel(todo)}-${index}`"
              :class="todo.status"
            >
              <CheckCircle2 v-if="todo.status === 'completed'" :size="16" />
              <Clock3 v-else-if="todo.status === 'in_progress'" :size="16" />
              <Circle v-else :size="16" />
              <div>
                <strong>{{ todoLabel(todo) }}</strong>
                <span>{{ statusText(todo.status) }}</span>
              </div>
            </li>
          </ol>
          <p v-else class="muted">
            任务开始后，Deep Agents 的 todos 会实时出现在这里。
          </p>
        </div>
      </section>

      <section v-show="activeInspector === 'flow'" class="panel">
        <header class="panel-header">
          <div>
            <h3>执行流</h3>
            <p>
              {{ toolEvents.length }} 个工具调用 ·
              {{ subagents.length }} 个子智能体
            </p>
          </div>
          <button
            class="icon-button"
            type="button"
            @click="collapsed.flow = !collapsed.flow"
          >
            <ChevronRight v-if="collapsed.flow" :size="16" />
            <ChevronDown v-else :size="16" />
          </button>
        </header>
        <div v-show="!collapsed.flow" class="panel-content timeline">
          <details
            v-for="event in toolEvents"
            :key="event.id"
            class="timeline-item"
          >
            <summary>
              <span><Wrench :size="15" /> {{ event.call.name }}</span>
              <small>{{ statusText(toolStatus(event)) }}</small>
            </summary>
            <p v-if="event.tool?.description">{{ event.tool.description }}</p>
            <pre>{{ JSON.stringify(event.call.args, null, 2) }}</pre>
            <p v-if="event.result" class="result-preview">
              {{ summarizeResult(event.result.content) }}
            </p>
          </details>

          <details
            v-for="subagent in subagents"
            :key="subagent.id"
            class="timeline-item subagent-timeline"
          >
            <summary>
              <span><Bot :size="15" /> {{ subagent.name || subagent.id }}</span>
              <small>{{ statusText(subagent.status) }}</small>
            </summary>
            <p>
              {{
                subagent.taskInput ||
                subagentByName.get(subagent.name) ||
                "子智能体任务"
              }}
            </p>
            <pre v-if="subagent.output || subagent.error">{{
              typeof (subagent.output || subagent.error) === "string"
                ? subagent.output || subagent.error
                : JSON.stringify(subagent.output || subagent.error, null, 2)
            }}</pre>
          </details>
          <p
            v-if="toolEvents.length === 0 && subagents.length === 0"
            class="muted"
          >
            工具调用和子智能体执行会随 stream 自动补进来。
          </p>
        </div>
      </section>

      <section v-show="activeInspector === 'artifacts'" class="panel">
        <header class="panel-header">
          <div>
            <h3>中间产物</h3>
            <p>{{ artifacts.length }} 个文件</p>
          </div>
          <button class="icon-button" type="button" @click="loadArtifacts">
            <RefreshCw :size="15" />
          </button>
        </header>
        <div class="panel-content split-panel">
          <div class="asset-list">
            <button
              v-for="item in artifacts"
              :key="item.path"
              type="button"
              class="asset-row"
              :class="{ active: selectedArtifact?.path === item.path }"
              @click="selectArtifact(item)"
            >
              <component :is="fileIcon(item)" :size="16" />
              <span>{{ item.name }}</span>
              <small>{{ item.kind }} · {{ humanSize(item.size) }}</small>
            </button>
            <p v-if="artifactError" class="error-text">{{ artifactError }}</p>
            <p v-if="artifacts.length === 0" class="muted">
              agent 生成博客、研究、故事或分析文件后，会自动出现在这里。
            </p>
          </div>
          <div class="preview-surface">
            <template v-if="selectedArtifact">
              <img
                v-if="selectedArtifactKind === 'image'"
                :src="assetUrl(selectedArtifact.path)"
                :alt="selectedArtifact.name"
              />
              <iframe
                v-else-if="selectedArtifactKind === 'pdf'"
                :src="assetUrl(selectedArtifact.path)"
                title="PDF preview"
              />
              <div
                v-else-if="
                  selectedArtifactKind === 'markdown' && artifactPayload
                "
                class="markdown-body"
                v-html="renderMarkdown(artifactPayload.content)"
              />
              <pre v-else-if="artifactPayload">{{
                artifactPayload.content
              }}</pre>
              <a
                v-else
                class="download-link"
                :href="assetUrl(selectedArtifact.path)"
                target="_blank"
                rel="noreferrer"
                >打开或下载原文件</a
              >
            </template>
            <p v-else class="muted">选择一个产物查看预览。</p>
          </div>
        </div>
      </section>

      <section v-show="activeInspector === 'sandbox'" class="panel">
        <header class="panel-header">
          <div>
            <h3>代码沙盒</h3>
            <p>{{ workspaceEntries.length }} 个可见条目</p>
          </div>
          <button class="icon-button" type="button" @click="loadWorkspace">
            <RefreshCw :size="15" />
          </button>
        </header>
        <div class="panel-content split-panel">
          <div class="file-tree">
            <button
              v-for="entry in workspaceEntries"
              :key="entry.path"
              type="button"
              :class="{ active: selectedFile?.path === entry.path }"
              :style="{ paddingLeft: `${12 + pathDepth(entry.path) * 14}px` }"
              @click="openWorkspaceFile(entry)"
            >
              <component :is="fileIcon(entry)" :size="15" />
              <span>{{ entry.name }}</span>
            </button>
            <p v-if="workspaceError" class="error-text">{{ workspaceError }}</p>
          </div>
          <div class="code-viewer">
            <template v-if="selectedFile">
              <header>
                <strong>{{ selectedFile.path }}</strong>
                <span
                  >{{ selectedFile.mime }} ·
                  {{ humanSize(selectedFile.size) }}</span
                >
              </header>
              <pre v-if="selectedFile.encoding === 'text'">{{
                selectedFile.content
              }}</pre>
              <div v-else class="binary-note">
                <Braces :size="20" /> 二进制文件以 base64
                读取，可通过产物面板打开。
              </div>
              <details v-if="selectedFile.encoding === 'text'" class="diff-box">
                <summary><Play :size="14" /> 与首次打开版本对比</summary>
                <pre><template v-for="(row, index) in fileDiff" :key="index"><span :class="row.kind">{{ row.kind === 'add' ? '+ ' : row.kind === 'remove' ? '- ' : '  ' }}{{ row.text }}</span>
</template></pre>
              </details>
            </template>
            <p v-else class="muted">选择文件查看代码、文本或执行生成的内容。</p>
          </div>
        </div>
      </section>
    </aside>
  </div>
</template>
