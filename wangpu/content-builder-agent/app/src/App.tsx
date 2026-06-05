import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  Camera,
  CheckCircle2,
  Circle,
  Code2,
  FileText,
  Folder,
  ImagePlus,
  ListChecks,
  LoaderCircle,
  MessageCircle,
  Mic,
  PanelLeftClose,
  PanelLeftOpen,
  Paperclip,
  Plus,
  RefreshCw,
  Send,
  Settings,
  Square,
  Terminal,
  Volume2,
  X,
} from "lucide-react";
import { Camera as CapacitorCamera, CameraResultType, CameraSource } from "@capacitor/camera";
import { useStream, type UseStreamOptions } from "@langchain/langgraph-sdk/react";
import type { Thread } from "@langchain/langgraph-sdk";
import type { SubagentStreamInterface } from "@langchain/langgraph-sdk/ui";
import {
  StreamingPcmPlayer,
  type VoiceEmotion,
  type VoicePlaybackState,
} from "./audio/pcmPlayer";
import { nextTextDelta } from "./audio/ttsDelta";
import { WavRecorder } from "./audio/wavRecorder";
import { isAssistant, messageText, type AppMessage } from "./chat/messages";
import { ArtifactPreview } from "./components/ArtifactPreview";
import { Collapsible, MessageCard, SubagentCard } from "./components/MessageCard";
import { LanAgentServerRuntime } from "./runtime/lanAgentServerRuntime";
import {
  loadConnectionSettings,
  loadThreadId,
  saveConnectionSettings,
  saveThreadId,
} from "./runtime/storage";
import type {
  ArtifactEntry,
  ConnectionSettings,
  KeyName,
  KeySettings,
  SandboxEntry,
} from "./runtime/types";
import { mergeSandboxEvent, type SandboxLog } from "./sandbox/events";

type Tab = "chat" | "tasks" | "artifacts" | "sandbox" | "settings";

interface Todo {
  content: string;
  status: "pending" | "in_progress" | "completed";
}

interface AgentState extends Record<string, unknown> {
  messages: unknown[];
  todos?: Todo[];
}

interface PendingImage {
  id: string;
  name: string;
  dataUrl: string;
  uploading: boolean;
}

type ContentBuilderStream = ReturnType<typeof useStream<AgentState>> & {
  subagents: Map<string, SubagentStreamInterface>;
  getSubagentsByMessage: (messageId: string) => SubagentStreamInterface[];
};

const DEFAULT_KEYS: KeySettings = {
  configured: { qwen: false, dashscope: false, tavily: false },
};

export default function App() {
  const [connection, setConnection] = useState(loadConnectionSettings);

  function saveConnection(next: ConnectionSettings) {
    saveConnectionSettings(next);
    setConnection({ baseUrl: next.baseUrl.trim(), pairingToken: next.pairingToken.trim() });
  }

  return (
    <AgentWorkspace
      key={`${connection.baseUrl}:${connection.pairingToken}`}
      connection={connection}
      onSaveConnection={saveConnection}
    />
  );
}

function AgentWorkspace({
  connection,
  onSaveConnection,
}: {
  connection: ConnectionSettings;
  onSaveConnection: (settings: ConnectionSettings) => void;
}) {
  const runtime = useMemo(() => new LanAgentServerRuntime(connection), [connection]);
  const [pairingState, setPairingState] = useState<"checking" | "ready" | "required">(
    connection.pairingToken ? "checking" : "required",
  );
  const [pairingMessage, setPairingMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    if (!runtime.connection.pairingToken) {
      setPairingState("required");
      setPairingMessage("");
      return;
    }
    setPairingState("checking");
    setPairingMessage("");
    void runtime.verifyPairing()
      .then((paired) => {
        if (!cancelled) {
          setPairingState(paired ? "ready" : "required");
          setPairingMessage(paired ? "" : "连接尚未就绪。请确认电脑端服务已启动，并填写当前显示的配对 Token。");
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setPairingState("required");
          setPairingMessage(`无法连接电脑端 Agent Server：${readError(error)}`);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [runtime]);

  if (pairingState !== "ready") {
    return (
      <PairingSetup
        checking={pairingState === "checking"}
        connection={connection}
        message={pairingMessage}
        onSaveConnection={onSaveConnection}
      />
    );
  }

  return (
    <AuthenticatedAgentWorkspace
      connection={connection}
      runtime={runtime}
      onSaveConnection={onSaveConnection}
    />
  );
}

function AuthenticatedAgentWorkspace({
  connection,
  runtime,
  onSaveConnection,
}: {
  connection: ConnectionSettings;
  runtime: LanAgentServerRuntime;
  onSaveConnection: (settings: ConnectionSettings) => void;
}) {
  const [activeTab, setActiveTab] = useState<Tab>("chat");
  const [threadId, setThreadId] = useState<string | null>(loadThreadId);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<PendingImage[]>([]);
  const [artifacts, setArtifacts] = useState<ArtifactEntry[]>([]);
  const [preview, setPreview] = useState<ArtifactEntry | null>(null);
  const [fullImage, setFullImage] = useState("");
  const [sandboxTree, setSandboxTree] = useState<SandboxEntry[]>([]);
  const [sandboxFile, setSandboxFile] = useState<{ path: string; content: string } | null>(null);
  const [sandboxLogs, setSandboxLogs] = useState<SandboxLog[]>([]);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [notice, setNotice] = useState("");
  const [recording, setRecording] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(() => typeof window === "undefined" || window.innerWidth > 760);
  const [voiceEmotion, setVoiceEmotion] = useState<VoiceEmotion | null>(null);
  const [voicePlayback, setVoicePlayback] = useState<VoicePlaybackState>("idle");
  const recorderRef = useRef<WavRecorder | undefined>(undefined);
  const ttsRef = useRef<StreamingPcmPlayer | undefined>(undefined);
  const ttsSeenText = useRef("");
  const speakResponse = useRef(false);
  const previousLoading = useRef(false);
  const lastHistorySnapshot = useRef("");
  const fileInput = useRef<HTMLInputElement>(null);

  const stream = useStream<AgentState>({
    apiUrl: runtime.connection.baseUrl,
    apiKey: runtime.connection.pairingToken || null,
    assistantId: "content_writer",
    threadId,
    onThreadId: (nextThreadId) => selectThreadState(nextThreadId),
    reconnectOnMount: Boolean(runtime.connection.pairingToken),
    fetchStateHistory: true,
    filterSubagentMessages: true,
    onCustomEvent: (event) => {
      setSandboxLogs((current) => mergeSandboxEvent(current, event));
      setRefreshNonce((current) => current + 1);
    },
    onFinish: () => setRefreshNonce((current) => current + 1),
    onError: (error) => setNotice(error instanceof Error ? error.message : String(error)),
  } as UseStreamOptions<AgentState> & { filterSubagentMessages: true }) as ContentBuilderStream;

  const messages = stream.messages as unknown as AppMessage[];
  const todos = stream.values.todos || [];
  const allSubagents = [...stream.subagents.values()] as SubagentStreamInterface[];

  useEffect(() => {
    if (!threadId || stream.isLoading || messages.length === 0) {
      return;
    }
    const lastMessage = messages[messages.length - 1];
    const snapshotKey = `${threadId}:${messages.length}:${lastMessage?.id || messageText(lastMessage)}`;
    if (snapshotKey === lastHistorySnapshot.current) {
      return;
    }
    lastHistorySnapshot.current = snapshotKey;
    void runtime.saveHistorySnapshot(threadId, messages, {
      source: "content-builder-app",
      saved_by: "mobile-client",
    }).catch((error) => {
      console.warn("Failed to save history snapshot", error);
    });
  }, [messages, runtime, stream.isLoading, threadId]);

  const refreshThreads = useCallback(async () => {
    if (!connection.pairingToken) {
      setThreads([]);
      return;
    }
    try {
      setThreads(await stream.client.threads.search({ limit: 50, sortBy: "updated_at", sortOrder: "desc" }));
    } catch (error) {
      setNotice(readError(error));
    }
  }, [connection.pairingToken, stream.client]);

  const refreshArtifacts = useCallback(async () => {
    if (!threadId) {
      setArtifacts([]);
      return;
    }
    try {
      setArtifacts(await runtime.listArtifacts(threadId));
    } catch (error) {
      setNotice(readError(error));
    }
  }, [runtime, threadId]);

  const refreshSandboxTree = useCallback(async () => {
    if (!threadId) {
      setSandboxTree([]);
      return;
    }
    try {
      setSandboxTree(await runtime.listSandboxTree(threadId));
    } catch (error) {
      setNotice(readError(error));
    }
  }, [runtime, threadId]);

  useEffect(() => {
    void refreshThreads();
  }, [refreshThreads, threadId, refreshNonce]);

  useEffect(() => {
    void refreshArtifacts();
    void refreshSandboxTree();
  }, [refreshArtifacts, refreshSandboxTree, refreshNonce]);

  useEffect(() => {
    if (!speakResponse.current) {
      return;
    }
    const latest = [...messages].reverse().find(isAssistant);
    const text = latest ? messageText(latest) : "";
    const delta = nextTextDelta(ttsSeenText.current, text);
    if (delta) {
      ttsSeenText.current = text;
      ttsRef.current?.sendText(delta);
    }
  }, [messages, stream.isLoading]);

  useEffect(() => {
    if (previousLoading.current && !stream.isLoading && speakResponse.current) {
      ttsRef.current?.flush();
      speakResponse.current = false;
    }
    previousLoading.current = stream.isLoading;
  }, [stream.isLoading]);

  function selectThreadState(nextThreadId: string | null) {
    setThreadId(nextThreadId);
    saveThreadId(nextThreadId);
  }

  function selectThread(nextThreadId: string) {
    stream.switchThread(nextThreadId);
    selectThreadState(nextThreadId);
    setAttachments([]);
    setSandboxFile(null);
    setActiveTab("chat");
    closeSidebarOnMobile();
  }

  function newConversation() {
    stream.switchThread(null);
    selectThreadState(null);
    setDraft("");
    setAttachments([]);
    setArtifacts([]);
    setSandboxTree([]);
    setSandboxFile(null);
    setSandboxLogs([]);
    setActiveTab("chat");
    closeSidebarOnMobile();
  }

  function closeSidebarOnMobile() {
    if (window.innerWidth <= 760) {
      setSidebarOpen(false);
    }
  }

  async function ensureThread(): Promise<string> {
    if (!runtime.connection.pairingToken) {
      setActiveTab("settings");
      throw new Error("请先在设置页填写电脑地址和局域网配对 Token。");
    }
    if (threadId) {
      return threadId;
    }
    const created = await stream.client.threads.create({
      metadata: { title: `新对话 ${new Date().toLocaleString()}`, source: "content-builder-app" },
    });
    stream.switchThread(created.thread_id);
    selectThreadState(created.thread_id);
    setThreads((current) => [created, ...current]);
    return created.thread_id;
  }

  async function prepareVoiceResponse(activeThreadId: string): Promise<void> {
    const player = new StreamingPcmPlayer(runtime.ttsSocketUrl(activeThreadId), {
      onEmotion: setVoiceEmotion,
      onState: setVoicePlayback,
    });
    const latest = [...messages].reverse().find(isAssistant);
    ttsRef.current?.close();
    ttsRef.current = player;
    speakResponse.current = true;
    ttsSeenText.current = latest ? messageText(latest) : "";
    setVoiceEmotion(null);
    try {
      await player.connect();
    } catch (error) {
      player.close();
      ttsRef.current = undefined;
      speakResponse.current = false;
      throw error;
    }
  }

  async function submitTurn(text = draft) {
    const cleaned = text.trim();
    if (!runtime.connection.pairingToken) {
      setNotice("请先在设置页填写电脑地址和局域网配对 Token。");
      setActiveTab("settings");
      return;
    }
    if ((!cleaned && attachments.length === 0) || stream.isLoading) {
      return;
    }
    void StreamingPcmPlayer.unlockAudio().catch(() => undefined);
    const activeThreadId = await ensureThread();
    try {
      await prepareVoiceResponse(activeThreadId);
    } catch (error) {
      setNotice(`文字回复仍会继续，语音播报不可用：${readError(error)}`);
    }
    setDraft("");
    const content = attachments.length
      ? [
          ...attachments.map((attachment) => ({ type: "image_url", image_url: { url: attachment.dataUrl } })),
          { type: "text", text: cleaned || "请分析这些图片。" },
        ]
      : cleaned;
    setAttachments([]);
    await stream.submit(
      { messages: [{ type: "human", content }] },
      { streamSubgraphs: true, streamResumable: true },
    );
  }

  async function addImage(file: File) {
    const activeThreadId = await ensureThread();
    const dataUrl = await readDataUrl(file);
    const pending: PendingImage = { id: crypto.randomUUID(), name: file.name, dataUrl, uploading: true };
    setAttachments((current) => [...current, pending]);
    try {
      await runtime.uploadImage(activeThreadId, file);
      setAttachments((current) => current.map((item) => item.id === pending.id ? { ...item, uploading: false } : item));
      setRefreshNonce((current) => current + 1);
    } catch (error) {
      setAttachments((current) => current.filter((item) => item.id !== pending.id));
      setNotice(readError(error));
    }
  }

  async function takePhoto() {
    try {
      const photo = await CapacitorCamera.getPhoto({
        quality: 88,
        resultType: CameraResultType.DataUrl,
        source: CameraSource.Camera,
      });
      if (photo.dataUrl) {
        await addImage(dataUrlToFile(photo.dataUrl, `camera-${Date.now()}.${photo.format || "jpeg"}`));
      }
    } catch (error) {
      setNotice(readError(error));
    }
  }

  async function startRecording() {
    if (recorderRef.current) {
      return;
    }
    try {
      void StreamingPcmPlayer.unlockAudio().catch(() => undefined);
      setVoiceEmotion(null);
      const recorder = new WavRecorder();
      await recorder.start();
      recorderRef.current = recorder;
      setRecording(true);
    } catch (error) {
      setNotice(`无法开始录音：${readError(error)}`);
    }
  }

  async function stopRecording() {
    if (!recorderRef.current) {
      return;
    }
    setRecording(false);
    try {
      const activeThreadId = await ensureThread();
      const wav = await recorderRef.current.stop();
      setNotice("正在识别语音...");
      const transcript = await runtime.transcribeAudio(activeThreadId, wav);
      setDraft(transcript);
      setNotice(`已识别：${transcript}`);
      await submitTurn(transcript);
    } catch (error) {
      setNotice(readError(error));
    } finally {
      recorderRef.current = undefined;
    }
  }

  async function openSandboxFile(entry: SandboxEntry) {
    if (!threadId || entry.type !== "file") {
      return;
    }
    try {
      setSandboxFile({ path: entry.path, content: await runtime.readSandboxFile(threadId, entry.path) });
    } catch (error) {
      setNotice(readError(error));
    }
  }

  return (
    <div className="app-shell">
      <aside className={`session-sidebar ${sidebarOpen ? "open" : "closed"}`}>
        <div className="sidebar-header">
          <div className="brand"><Bot size={21} /><strong>Content Builder</strong></div>
          <button className="sidebar-close" aria-label="收起会话栏" onClick={() => setSidebarOpen(false)} title="收起会话栏"><PanelLeftClose /></button>
        </div>
        <button className="primary-button wide" onClick={newConversation}><Plus size={17} /> 新建对话</button>
        <div className="sidebar-label">历史会话</div>
        <div className="session-list">
          {threads.map((thread) => (
            <button
              className={`session-item ${thread.thread_id === threadId ? "active" : ""}`}
              key={thread.thread_id}
              onClick={() => selectThread(thread.thread_id)}
            >
              <strong>{String(thread.metadata?.title || "内容创作会话")}</strong>
              <small>{new Date(thread.updated_at).toLocaleString()}</small>
            </button>
          ))}
        </div>
      </aside>
      {sidebarOpen && <button className="sidebar-backdrop" aria-label="关闭会话栏" onClick={() => setSidebarOpen(false)} />}
      <main className="workspace">
        <header className="topbar">
          <div>
            <strong>{activeTabTitle(activeTab)}</strong>
            <small>{threadId ? `会话 ${threadId.slice(0, 8)}` : "新对话将在发送时创建"}</small>
          </div>
          <div className="row">
            <button className="icon-button" aria-label="切换会话栏" onClick={() => setSidebarOpen((open) => !open)} title={sidebarOpen ? "收起会话栏" : "展开会话栏"}>
              {sidebarOpen ? <PanelLeftClose /> : <PanelLeftOpen />}
            </button>
            <button className="icon-button mobile-new" onClick={newConversation} title="新建对话"><Plus /></button>
            <button className="icon-button" onClick={() => setRefreshNonce((value) => value + 1)} title="刷新"><RefreshCw /></button>
          </div>
        </header>
        {notice && <div className="notice"><span>{notice}</span><button onClick={() => setNotice("")}><X size={15} /></button></div>}
        {voiceEmotion && (
          <div className={`voice-emotion ${voicePlayback}`}>
            <span className="voice-emoji" aria-label={voiceEmotion.emotion_cn}>{voiceEmotion.emoji}</span>
            <Volume2 className={voicePlayback === "playing" ? "voice-pulse" : ""} />
            <strong>{voiceEmotion.emotion_cn}</strong>
            <span>{voiceEmotion.text}</span>
          </div>
        )}
        <div className="tab-content">
          {activeTab === "chat" && (
            <ChatTab
              attachments={attachments}
              draft={draft}
              isLoading={Boolean(runtime.connection.pairingToken) && stream.isLoading}
              messages={messages}
              recording={recording}
              stream={stream}
              onAttachmentRemove={(id) => setAttachments((current) => current.filter((item) => item.id !== id))}
              onDraft={setDraft}
              onFilePick={() => fileInput.current?.click()}
              onImageOpen={setFullImage}
              onRecordStart={() => void startRecording()}
              onRecordStop={() => void stopRecording()}
              onSend={() => void submitTurn()}
              onStop={() => void stream.stop()}
              onTakePhoto={() => void takePhoto()}
            />
          )}
          {activeTab === "tasks" && <TasksTab todos={todos} subagents={allSubagents} />}
          {activeTab === "artifacts" && <ArtifactsTab artifacts={artifacts} onOpen={setPreview} />}
          {activeTab === "sandbox" && (
            <SandboxTab
              file={sandboxFile}
              logs={sandboxLogs}
              tree={sandboxTree}
              onFileOpen={(entry) => void openSandboxFile(entry)}
            />
          )}
          {activeTab === "settings" && (
            <SettingsTab connection={connection} runtime={runtime} onSaveConnection={onSaveConnection} />
          )}
        </div>
        <nav className="bottom-nav">
          <NavButton active={activeTab === "chat"} icon={<MessageCircle />} label="对话" onClick={() => setActiveTab("chat")} />
          <NavButton active={activeTab === "tasks"} icon={<ListChecks />} label="任务" onClick={() => setActiveTab("tasks")} />
          <NavButton active={activeTab === "artifacts"} icon={<FileText />} label="产物" onClick={() => setActiveTab("artifacts")} />
          <NavButton active={activeTab === "sandbox"} icon={<Terminal />} label="沙盒" onClick={() => setActiveTab("sandbox")} />
          <NavButton active={activeTab === "settings"} icon={<Settings />} label="设置" onClick={() => setActiveTab("settings")} />
        </nav>
      </main>
      <input
        accept="image/*"
        hidden
        multiple
        ref={fileInput}
        type="file"
        onChange={(event) => {
          Array.from(event.target.files || []).forEach((file) => void addImage(file));
          event.target.value = "";
        }}
      />
      {preview && <ArtifactPreview artifact={preview} runtime={runtime} onClose={() => setPreview(null)} />}
      {fullImage && (
        <div className="preview-overlay" role="dialog" aria-label="查看图片">
          <header><strong>图片预览</strong><button className="icon-button" onClick={() => setFullImage("")}><X /></button></header>
          <main><img className="full-image" src={fullImage} alt="完整预览" /></main>
        </div>
      )}
    </div>
  );
}

function ChatTab({
  attachments,
  draft,
  isLoading,
  messages,
  recording,
  stream,
  onAttachmentRemove,
  onDraft,
  onFilePick,
  onImageOpen,
  onRecordStart,
  onRecordStop,
  onSend,
  onStop,
  onTakePhoto,
}: {
  attachments: PendingImage[];
  draft: string;
  isLoading: boolean;
  messages: AppMessage[];
  recording: boolean;
  stream: ContentBuilderStream;
  onAttachmentRemove: (id: string) => void;
  onDraft: (value: string) => void;
  onFilePick: () => void;
  onImageOpen: (url: string) => void;
  onRecordStart: () => void;
  onRecordStop: () => void;
  onSend: () => void;
  onStop: () => void;
  onTakePhoto: () => void;
}) {
  return (
    <div className="chat-layout">
      <div className="message-feed">
        {messages.length === 0 && (
          <div className="empty-state">
            <Bot size={38} />
            <h2>开始一次内容创作</h2>
            <p>发送文字、照片或语音。任务规划、子智能体执行、文件和沙盒过程会同步展开。</p>
          </div>
        )}
        {messages.map((message, index) => (
          <MessageCard
            key={message.id || `message-${index}`}
            message={message}
            onImageOpen={onImageOpen}
            subagents={message.id ? stream.getSubagentsByMessage(message.id) : []}
          />
        ))}
        {isLoading && <div className="streaming-line"><LoaderCircle className="spin" size={16} /> 正在流式生成...</div>}
      </div>
      <div className="composer">
        {attachments.length > 0 && (
          <div className="attachment-row">
            {attachments.map((attachment) => (
              <div className="attachment" key={attachment.id}>
                <img src={attachment.dataUrl} alt={attachment.name} />
                {attachment.uploading && <LoaderCircle className="spin attachment-loader" size={16} />}
                <button onClick={() => onAttachmentRemove(attachment.id)}><X size={14} /></button>
              </div>
            ))}
          </div>
        )}
        <textarea
          placeholder="描述你想创作的内容..."
          rows={2}
          value={draft}
          onChange={(event) => onDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSend();
            }
          }}
        />
        <div className="composer-actions">
          <div className="row">
            <button className="icon-button" onClick={onFilePick} title="从相册选择"><Paperclip /></button>
            <button className="icon-button" onClick={onTakePhoto} title="拍照"><Camera /></button>
            <button
              className={`icon-button ${recording ? "recording" : ""}`}
              onPointerDown={onRecordStart}
              onPointerCancel={onRecordStop}
              onPointerUp={onRecordStop}
              onPointerLeave={() => recording && onRecordStop()}
              title="按住说话"
            >
              <Mic />
            </button>
          </div>
          {isLoading
            ? <button className="send-button stop" onClick={onStop}><Square size={16} /> 停止</button>
            : <button className="send-button" onClick={onSend}><Send size={16} /> 发送</button>}
        </div>
      </div>
    </div>
  );
}

function TasksTab({ todos, subagents }: { todos: Todo[]; subagents: SubagentStreamInterface[] }) {
  return (
    <div className="content-stack">
      <section className="panel">
        <h2><ListChecks /> 任务规划</h2>
        {todos.length === 0 && <p className="muted">发送复杂请求后，主智能体的规划会在这里实时更新。</p>}
        {todos.map((todo, index) => (
          <div className={`todo ${todo.status}`} key={`${todo.content}-${index}`}>
            {todo.status === "completed" ? <CheckCircle2 /> : todo.status === "in_progress" ? <LoaderCircle className="spin" /> : <Circle />}
            <span>{todo.content}</span><small>{todo.status}</small>
          </div>
        ))}
      </section>
      <section className="panel">
        <h2><Bot /> 子智能体执行流</h2>
        {subagents.length === 0 && <p className="muted">发生任务委派时，子智能体会按会话展示在这里。</p>}
        {subagents.map((subagent) => <SubagentCard key={subagent.id} subagent={subagent} />)}
      </section>
    </div>
  );
}

function ArtifactsTab({ artifacts, onOpen }: { artifacts: ArtifactEntry[]; onOpen: (artifact: ArtifactEntry) => void }) {
  return (
    <div className="artifact-grid">
      {artifacts.length === 0 && <div className="empty-state"><ImagePlus size={34} /><h2>当前会话暂无中间产物</h2><p>生成的图片、文档和小游戏会严格归入当前会话。</p></div>}
      {artifacts.map((artifact) => (
        <button className="artifact-card" key={artifact.path} onClick={() => onOpen(artifact)}>
          <div className="artifact-icon">{artifact.kind === "image" ? <ImagePlus /> : artifact.kind === "html" ? <Code2 /> : <FileText />}</div>
          <strong>{artifact.name}</strong>
          <small>{formatBytes(artifact.size)} · {new Date(artifact.modified_at * 1000).toLocaleString()}</small>
        </button>
      ))}
    </div>
  );
}

function SandboxTab({
  file,
  logs,
  tree,
  onFileOpen,
}: {
  file: { path: string; content: string } | null;
  logs: SandboxLog[];
  tree: SandboxEntry[];
  onFileOpen: (entry: SandboxEntry) => void;
}) {
  return (
    <div className="sandbox-layout">
      <section className="panel sandbox-tree">
        <h2><Folder /> 文件树</h2>
        {tree.map((entry) => (
          <button className={`tree-entry ${entry.type}`} key={entry.path} onClick={() => onFileOpen(entry)}>
            {entry.type === "directory" ? <Folder /> : <FileText />}<span>{entry.path}</span>
          </button>
        ))}
      </section>
      <section className="panel">
        <h2><Code2 /> 代码预览</h2>
        {file ? <><small>{file.path}</small><pre className="code-preview"><code>{file.content}</code></pre></> : <p className="muted">选择文本文件查看内容。</p>}
      </section>
      <section className="panel sandbox-logs">
        <h2><Terminal /> 命令执行流</h2>
        {logs.length === 0 && <p className="muted">可信局域网模式下的白名单命令执行会流式显示在这里。</p>}
        {logs.map((log) => (
          <Collapsible key={log.id} open={log.status === "running"} title={`${log.status} · ${log.command}`}>
            {log.stdout && <pre>{log.stdout}</pre>}
            {log.stderr && <pre className="stderr">{log.stderr}</pre>}
            <small>
              退出码 {log.exitCode ?? "执行中"} · {log.finishedAt ? `${log.finishedAt - log.startedAt}ms` : "流式输出中"}
            </small>
          </Collapsible>
        ))}
      </section>
    </div>
  );
}

function PairingSetup({
  checking,
  connection,
  message,
  onSaveConnection,
}: {
  checking: boolean;
  connection: ConnectionSettings;
  message: string;
  onSaveConnection: (settings: ConnectionSettings) => void;
}) {
  const [form, setForm] = useState(connection);

  return (
    <main className="pairing-shell">
      <section className="panel pairing-card settings-page">
        <div className="brand pairing-brand"><Bot size={22} /><strong>Content Builder</strong></div>
        <h1>连接电脑端 Agent Server</h1>
        <p className="muted">首次使用或 Token 变化后，请输入电脑端启动脚本显示的局域网配对 Token。验证成功后才会加载历史会话与流式事件。</p>
        <label>
          电脑 Agent Server 地址
          <input
            value={form.baseUrl}
            onChange={(event) => setForm({ ...form, baseUrl: event.target.value })}
            placeholder="http://192.168.1.8:2024"
          />
        </label>
        <label>
          局域网配对 Token
          <input
            type="password"
            value={form.pairingToken}
            onChange={(event) => setForm({ ...form, pairingToken: event.target.value })}
          />
        </label>
        {message && <p className="pairing-error">{message}</p>}
        <button className="primary-button" disabled={checking} onClick={() => onSaveConnection(form)}>
          {checking ? <><LoaderCircle className="spin" size={16} /> 正在验证...</> : "保存并验证连接"}
        </button>
        <p className="muted pairing-note">电脑端运行 <code>scripts/dev-backend.ps1</code> 后会显示 Token。模型 Key 只保存在电脑端，不会写入 APK。</p>
      </section>
    </main>
  );
}

function SettingsTab({
  connection,
  runtime,
  onSaveConnection,
}: {
  connection: ConnectionSettings;
  runtime: LanAgentServerRuntime;
  onSaveConnection: (settings: ConnectionSettings) => void;
}) {
  const [form, setForm] = useState(connection);
  const [keys, setKeys] = useState<KeySettings>(DEFAULT_KEYS);
  const [keyInputs, setKeyInputs] = useState<Record<KeyName, string>>({ qwen: "", dashscope: "", tavily: "" });
  const [status, setStatus] = useState("");

  const loadKeys = useCallback(async () => {
    if (!runtime.connection.pairingToken) {
      return;
    }
    try {
      setKeys(await runtime.getKeyStatus());
    } catch (error) {
      setStatus(readError(error));
    }
  }, [runtime]);

  useEffect(() => {
    void loadKeys();
  }, [loadKeys]);

  async function updateKeys() {
    try {
      setKeys(await runtime.updateKeys(keyInputs));
      setKeyInputs({ qwen: "", dashscope: "", tavily: "" });
      setStatus("Key 已写入电脑端本地配置，前端不会读取或显示明文。");
    } catch (error) {
      setStatus(readError(error));
    }
  }

  async function verifyKeys() {
    try {
      const result = await runtime.verifyKeys();
      setKeys(result);
      setStatus(result.message || (result.ready ? "配置可用。" : "配置不完整。"));
    } catch (error) {
      setStatus(readError(error));
    }
  }

  return (
    <div className="content-stack settings-page">
      <section className="panel">
        <h2><Settings /> 局域网连接</h2>
        <label>电脑 Agent Server 地址<input value={form.baseUrl} onChange={(event) => setForm({ ...form, baseUrl: event.target.value })} placeholder="http://192.168.1.8:2024" /></label>
        <label>局域网配对 Token<input type="password" value={form.pairingToken} onChange={(event) => setForm({ ...form, pairingToken: event.target.value })} /></label>
        <p className="muted">Web 预览仅在当前浏览器会话保存 Token；模型 Key 从不保存到手机。第二阶段内嵌 runtime 将改用 Android Keystore。</p>
        <button className="primary-button" onClick={() => onSaveConnection(form)}>保存连接</button>
      </section>
      <section className="panel">
        <h2><Bot /> 模型与工具 Key</h2>
        {(["qwen", "dashscope", "tavily"] as KeyName[]).map((name) => (
          <label key={name}>
            <span className="key-label">{name}<small className={keys.configured[name] ? "configured" : ""}>{keys.configured[name] ? "已配置" : "未配置"}</small></span>
            <input type="password" value={keyInputs[name]} onChange={(event) => setKeyInputs({ ...keyInputs, [name]: event.target.value })} placeholder="留空则不修改" />
          </label>
        ))}
        <div className="row wrap">
          <button className="primary-button" disabled={!connection.pairingToken} onClick={() => void updateKeys()}>更新 Key</button>
          <button className="secondary-button" disabled={!connection.pairingToken} onClick={() => void verifyKeys()}>验证配置</button>
        </div>
        {status && <p className="muted">{status}</p>}
      </section>
      <section className="panel warning-panel">
        <h2><Terminal /> 开发模式提醒</h2>
        <p>当前 App 连接电脑上的局域网 Agent Server。白名单 LocalShellBackend 仍会以电脑当前用户权限执行命令，它不是安全隔离沙盒。仅在可信局域网与开发设备上使用。</p>
      </section>
    </div>
  );
}

function NavButton({ active, icon, label, onClick }: { active: boolean; icon: React.ReactNode; label: string; onClick: () => void }) {
  return <button className={active ? "active" : ""} onClick={onClick}>{icon}<span>{label}</span></button>;
}

function activeTabTitle(tab: Tab): string {
  return { chat: "对话", tasks: "任务与子智能体", artifacts: "中间产物", sandbox: "代码沙盒", settings: "设置" }[tab];
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function readError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function readDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function dataUrlToFile(dataUrl: string, name: string): File {
  const [metadata, data] = dataUrl.split(",");
  const mime = metadata.match(/:(.*?);/)?.[1] || "image/jpeg";
  const bytes = Uint8Array.from(atob(data), (character) => character.charCodeAt(0));
  return new File([bytes], name, { type: mime });
}
