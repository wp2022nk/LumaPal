export type KeyName = "qwen" | "dashscope" | "tavily";

export interface ConnectionSettings {
  baseUrl: string;
  pairingToken: string;
}

export interface KeySettings {
  configured: Record<KeyName, boolean>;
  ready?: boolean;
  message?: string;
}

export type ArtifactKind = "image" | "pdf" | "html" | "text" | "download";

export interface ArtifactEntry {
  artifact_id?: string;
  name: string;
  title?: string;
  path: string;
  size: number;
  modified_at: number;
  mime_type: string;
  kind: ArtifactKind;
  preview_url: string;
}

export type HistoryArtifactCategory = "storybook" | "audiobook" | "game" | "growth_report" | "image" | "document";

export interface HistoryArtifactEntry extends ArtifactEntry {
  title: string;
  date: string;
  source: "history" | "output" | "roadshow" | "legacy";
  category: HistoryArtifactCategory;
  archive_path?: string;
}

export interface ArchivedChatMessage {
  id: string;
  role: "user" | "agent";
  content: string;
  created_at?: string;
  source?: string;
}

export interface TodoEntry {
  content: string;
  status: "pending" | "in_progress" | "completed";
}

export interface RuntimeEventEntry {
  id?: string;
  type: string;
  event_type?: string;
  source?: string;
  text?: string;
  recorded_at?: string;
  todos?: TodoEntry[];
  subagent_id?: string;
  status?: string;
  tool_name?: string;
}

export interface ArchivedSubagent {
  id?: string;
  subagent_id?: string;
  source?: string;
  status?: string;
  text?: string;
  tool_name?: string;
  args_preview?: unknown;
}

export interface ThreadArchiveState {
  thread_id: string;
  chat: ArchivedChatMessage[];
  artifacts: ArtifactEntry[];
  recent_events: RuntimeEventEntry[];
  todos: TodoEntry[];
  subagents: ArchivedSubagent[];
}

export interface SandboxEntry {
  name: string;
  path: string;
  type: "directory" | "file";
  size: number;
}

export interface AgentRuntime {
  readonly kind: "lan" | "embedded";
  readonly connection: ConnectionSettings;
  verifyPairing(): Promise<boolean>;
  getKeyStatus(): Promise<KeySettings>;
  updateKeys(keys: Partial<Record<KeyName, string>>): Promise<KeySettings>;
  verifyKeys(): Promise<KeySettings>;
  uploadImage(threadId: string, image: File): Promise<ArtifactEntry>;
  transcribeAudio(threadId: string, audio: Blob): Promise<string>;
  listArtifacts(threadId: string): Promise<ArtifactEntry[]>;
  getThreadState(threadId: string): Promise<ThreadArchiveState>;
  listHistoryArtifacts(filters?: { startDate?: string; endDate?: string }): Promise<HistoryArtifactEntry[]>;
  listSandboxTree(threadId: string): Promise<SandboxEntry[]>;
  readSandboxFile(threadId: string, path: string): Promise<string>;
  saveHistorySnapshot(threadId: string, messages: unknown[], metadata?: Record<string, unknown>): Promise<void>;
  absoluteUrl(path: string): string;
  ttsSocketUrl(threadId: string): string;
  appEventsUrl(): string;
  hardwareEventsUrl(threadId: string): string;
  getHardwareStatus(): Promise<{ sessions: Array<{ session_id: string; thread_id: string; device_id: string; tools: string[] }> }>;
}
