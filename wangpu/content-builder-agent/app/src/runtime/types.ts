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
  name: string;
  path: string;
  size: number;
  modified_at: number;
  mime_type: string;
  kind: ArtifactKind;
  preview_url: string;
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
  listSandboxTree(threadId: string): Promise<SandboxEntry[]>;
  readSandboxFile(threadId: string, path: string): Promise<string>;
  saveHistorySnapshot(threadId: string, messages: unknown[], metadata?: Record<string, unknown>): Promise<void>;
  absoluteUrl(path: string): string;
  ttsSocketUrl(threadId: string): string;
  hardwareEventsUrl(threadId: string): string;
  getHardwareStatus(): Promise<{ sessions: Array<{ session_id: string; thread_id: string; device_id: string; tools: string[] }> }>;
}
