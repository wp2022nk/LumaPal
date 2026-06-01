export interface VoiceEmotion {
  text: string;
  emotion_en: string;
  emotion_cn: string;
  emoji: string;
  confidence: number;
}

export type VoicePlaybackState = "connecting" | "ready" | "playing" | "idle" | "error";

interface StreamingPcmPlayerOptions {
  onEmotion?: (emotion: VoiceEmotion) => void;
  onState?: (state: VoicePlaybackState) => void;
}

export class StreamingPcmPlayer {
  private static sharedContext?: AudioContext;

  private socket?: WebSocket;
  private context?: AudioContext;
  private connecting?: Promise<void>;
  private sampleRate = 24_000;
  private nextStart = 0;
  private pendingMessages: Record<string, unknown>[] = [];

  constructor(
    private readonly socketUrl: string,
    private readonly options: StreamingPcmPlayerOptions = {},
  ) {}

  static async unlockAudio(): Promise<AudioContext> {
    const context = this.sharedContext || new AudioContext();
    this.sharedContext = context;
    if (context.state === "suspended") {
      await context.resume();
    }
    return context;
  }

  async connect(): Promise<void> {
    if (this.socket?.readyState === WebSocket.OPEN) {
      return;
    }
    if (this.connecting) {
      return this.connecting;
    }
    this.context = await StreamingPcmPlayer.unlockAudio();
    this.options.onState?.("connecting");
    this.socket = new WebSocket(this.socketUrl);
    this.socket.binaryType = "arraybuffer";
    this.socket.onmessage = (event) => this.onMessage(event);
    this.connecting = new Promise<void>((resolve, reject) => {
      if (!this.socket) {
        reject(new Error("Unable to open TTS WebSocket."));
        return;
      }
      this.socket.onopen = () => {
        this.pendingMessages.splice(0).forEach((payload) => this.send(payload));
        this.options.onState?.("ready");
        resolve();
      };
      this.socket.onerror = () => {
        this.options.onState?.("error");
        reject(new Error("Unable to connect to TTS."));
      };
    }).finally(() => {
      this.connecting = undefined;
    });
    return this.connecting;
  }

  sendText(text: string): void {
    if (text) {
      this.sendOrQueue({ type: "text", text });
    }
  }

  flush(): void {
    this.sendOrQueue({ type: "flush" });
  }

  close(): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.send({ type: "cancel" });
    }
    this.socket?.close();
    this.socket = undefined;
    this.pendingMessages = [];
    this.nextStart = 0;
    this.options.onState?.("idle");
  }

  private sendOrQueue(payload: Record<string, unknown>): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      this.pendingMessages.push(payload);
      return;
    }
    this.send(payload);
  }

  private send(payload: Record<string, unknown>): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify(payload));
    }
  }

  private onMessage(event: MessageEvent): void {
    if (typeof event.data === "string") {
      this.onJsonMessage(event.data);
      return;
    }
    if (!(event.data instanceof ArrayBuffer) || !this.context) {
      return;
    }
    if (this.context.state === "suspended") {
      void this.context.resume();
    }
    const pcm = new Int16Array(event.data);
    const buffer = this.context.createBuffer(1, pcm.length, this.sampleRate);
    const channel = buffer.getChannelData(0);
    pcm.forEach((sample, index) => {
      channel[index] = sample / 0x8000;
    });
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    this.nextStart = Math.max(this.context.currentTime, this.nextStart);
    source.start(this.nextStart);
    this.nextStart += buffer.duration;
    this.options.onState?.("playing");
  }

  private onJsonMessage(raw: string): void {
    const payload = JSON.parse(raw) as {
      type?: string;
      sample_rate?: number;
      message?: string;
    } & Partial<VoiceEmotion>;
    if (payload.type === "ready" && payload.sample_rate) {
      this.sampleRate = payload.sample_rate;
      this.options.onState?.("ready");
    } else if (payload.type === "emotion") {
      this.options.onEmotion?.({
        text: String(payload.text || ""),
        emotion_en: String(payload.emotion_en || "neutral"),
        emotion_cn: String(payload.emotion_cn || "中性"),
        emoji: String(payload.emoji || "😶"),
        confidence: Number(payload.confidence || 0),
      });
    } else if (payload.type === "segment_end" || payload.type === "complete" || payload.type === "cancelled") {
      this.options.onState?.("idle");
    } else if (payload.type === "error") {
      this.options.onState?.("error");
    }
  }
}
