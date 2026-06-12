import type {
  AgentRuntime,
  ArtifactEntry,
  ConnectionSettings,
  KeyName,
  KeySettings,
  SandboxEntry,
} from "./types";

/**
 * Phase-two contract. The UI only depends on AgentRuntime, so a private-file
 * TypeScript createDeepAgent() runtime can replace the LAN server later.
 */
export class EmbeddedDeepAgentsRuntime implements AgentRuntime {
  readonly kind = "embedded";
  readonly connection: ConnectionSettings = { baseUrl: "", pairingToken: "" };

  absoluteUrl(path: string): string {
    return path;
  }

  ttsSocketUrl(): string {
    return this.unsupported();
  }

  appEventsUrl(): string {
    return this.unsupported();
  }

  hardwareEventsUrl(): string {
    return this.unsupported();
  }

  verifyPairing(): Promise<boolean> {
    return Promise.resolve(true);
  }

  getKeyStatus(): Promise<KeySettings> {
    return this.unsupported();
  }

  updateKeys(_keys: Partial<Record<KeyName, string>>): Promise<KeySettings> {
    return this.unsupported();
  }

  verifyKeys(): Promise<KeySettings> {
    return this.unsupported();
  }

  getHardwareStatus(): Promise<{ sessions: Array<{ session_id: string; thread_id: string; device_id: string; tools: string[] }> }> {
    return this.unsupported();
  }

  uploadImage(_threadId: string, _image: File): Promise<ArtifactEntry> {
    return this.unsupported();
  }

  transcribeAudio(_threadId: string, _audio: Blob): Promise<string> {
    return this.unsupported();
  }

  listArtifacts(_threadId: string): Promise<ArtifactEntry[]> {
    return this.unsupported();
  }

  listSandboxTree(_threadId: string): Promise<SandboxEntry[]> {
    return this.unsupported();
  }

  readSandboxFile(_threadId: string, _path: string): Promise<string> {
    return this.unsupported();
  }

  saveHistorySnapshot(_threadId: string, _messages: unknown[]): Promise<void> {
    return Promise.resolve();
  }

  private unsupported(): never {
    throw new Error("Embedded Deep Agents runtime is reserved for phase two.");
  }
}
