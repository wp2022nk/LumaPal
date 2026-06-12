import type {
  AgentRuntime,
  ArtifactEntry,
  ConnectionSettings,
  HistoryArtifactEntry,
  KeyName,
  KeySettings,
  SandboxEntry,
} from "./types";

export function normalizeBaseUrl(url: string): string {
  const trimmed = url.trim().replace(/\/+$/, "");
  return trimmed || "http://127.0.0.1:2024";
}

export class LanAgentServerRuntime implements AgentRuntime {
  readonly kind = "lan";
  readonly connection: ConnectionSettings;

  constructor(connection: ConnectionSettings) {
    this.connection = {
      baseUrl: normalizeBaseUrl(connection.baseUrl),
      pairingToken: connection.pairingToken.trim(),
    };
  }

  absoluteUrl(path: string): string {
    if (/^[a-z][a-z0-9+.-]*:/i.test(path)) {
      return path;
    }
    return `${this.connection.baseUrl}${path.startsWith("/") ? path : `/${path}`}`;
  }

  ttsSocketUrl(threadId: string): string {
    const base = this.connection.baseUrl.replace(/^http/i, "ws");
    const token = encodeURIComponent(this.connection.pairingToken);
    return `${base}/api/content-builder/threads/${encodeURIComponent(threadId)}/voice/tts?token=${token}`;
  }

  hardwareEventsUrl(threadId: string): string {
    const token = encodeURIComponent(this.connection.pairingToken);
    return this.absoluteUrl(`/api/xiaozhi/v1/threads/${encodeURIComponent(threadId)}/events?token=${token}`);
  }

  appEventsUrl(): string {
    const token = encodeURIComponent(this.connection.pairingToken);
    return this.absoluteUrl(`/api/content-builder/events?token=${token}`);
  }

  async verifyPairing(): Promise<boolean> {
    const response = await this.request<{ paired: boolean; agent_server_ready: boolean }>(
      "/api/content-builder/gateway/status",
    );
    return response.paired && response.agent_server_ready;
  }

  async getKeyStatus(): Promise<KeySettings> {
    return this.request("/api/content-builder/settings/keys");
  }

  async updateKeys(keys: Partial<Record<KeyName, string>>): Promise<KeySettings> {
    return this.request("/api/content-builder/settings/keys", {
      method: "PUT",
      body: JSON.stringify(keys),
      headers: { "content-type": "application/json" },
    });
  }

  async verifyKeys(): Promise<KeySettings> {
    return this.request("/api/content-builder/settings/keys/verify", { method: "POST" });
  }

  async getHardwareStatus(): Promise<{ sessions: Array<{ session_id: string; thread_id: string; device_id: string; tools: string[] }> }> {
    return this.request("/api/xiaozhi/v1/status");
  }

  async uploadImage(threadId: string, image: File): Promise<ArtifactEntry> {
    const form = new FormData();
    form.append("image", image);
    return this.request(
      `/api/content-builder/threads/${encodeURIComponent(threadId)}/uploads/images`,
      { method: "POST", body: form },
    );
  }

  async transcribeAudio(threadId: string, audio: Blob): Promise<string> {
    const form = new FormData();
    form.append("audio", audio, "recording.wav");
    const response = await this.request<{ transcript: string }>(
      `/api/content-builder/threads/${encodeURIComponent(threadId)}/voice/asr`,
      { method: "POST", body: form },
    );
    return response.transcript;
  }

  async listArtifacts(threadId: string): Promise<ArtifactEntry[]> {
    const response = await this.request<{ entries: ArtifactEntry[] }>(
      `/api/content-builder/threads/${encodeURIComponent(threadId)}/artifacts`,
    );
    return response.entries;
  }

  async listHistoryArtifacts(filters: { startDate?: string; endDate?: string } = {}): Promise<HistoryArtifactEntry[]> {
    const params = new URLSearchParams();
    if (filters.startDate) {
      params.set("start_date", filters.startDate);
    }
    if (filters.endDate) {
      params.set("end_date", filters.endDate);
    }
    const query = params.toString();
    const response = await this.request<{ entries: HistoryArtifactEntry[] }>(
      `/api/content-builder/history/artifacts${query ? `?${query}` : ""}`,
    );
    return response.entries;
  }

  async listSandboxTree(threadId: string): Promise<SandboxEntry[]> {
    const response = await this.request<{ entries: SandboxEntry[] }>(
      `/api/content-builder/threads/${encodeURIComponent(threadId)}/sandbox/tree`,
    );
    return response.entries;
  }

  async readSandboxFile(threadId: string, path: string): Promise<string> {
    const response = await this.request<{ content: string }>(
      `/api/content-builder/threads/${encodeURIComponent(threadId)}/sandbox/file?path=${encodeURIComponent(path)}`,
    );
    return response.content;
  }

  async saveHistorySnapshot(
    threadId: string,
    messages: unknown[],
    metadata: Record<string, unknown> = {},
  ): Promise<void> {
    await this.request(
      `/api/content-builder/threads/${encodeURIComponent(threadId)}/history/snapshot`,
      {
        method: "POST",
        body: JSON.stringify({ messages, metadata }),
        headers: { "content-type": "application/json" },
      },
    );
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers = this.headers(options.headers);
    const response = await fetch(this.absoluteUrl(path), { ...options, headers });
    if (!response.ok) {
      throw await responseError(response);
    }
    return response.json() as Promise<T>;
  }

  private headers(initial?: HeadersInit): Headers {
    const headers = new Headers(initial);
    if (this.connection.pairingToken) {
      headers.set("x-api-key", this.connection.pairingToken);
    }
    return headers;
  }
}

async function responseError(response: Response): Promise<Error> {
  const body = await response.json().catch(() => ({ detail: response.statusText }));
  return new Error(String(body.detail || body.message || response.statusText));
}
