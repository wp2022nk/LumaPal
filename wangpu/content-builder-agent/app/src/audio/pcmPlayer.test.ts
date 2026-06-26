import { afterEach, describe, expect, it, vi } from "vitest";
import { StreamingPcmPlayer } from "./pcmPlayer";

describe("StreamingPcmPlayer", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    (StreamingPcmPlayer as unknown as { sharedContext?: AudioContext }).sharedContext = undefined;
  });

  it("queues text and flush until connected and forwards emotion events", async () => {
    class FakeAudioContext {
      state = "suspended";
      resume = vi.fn(async () => {
        this.state = "running";
      });
    }

    class FakeWebSocket {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static instances: FakeWebSocket[] = [];

      binaryType = "";
      onerror?: () => void;
      onmessage?: (event: MessageEvent) => void;
      onopen?: () => void;
      readyState = FakeWebSocket.CONNECTING;
      sent: string[] = [];

      constructor(readonly url: string) {
        FakeWebSocket.instances.push(this);
      }

      close() {
        this.readyState = 3;
      }

      open() {
        this.readyState = FakeWebSocket.OPEN;
        this.onopen?.();
      }

      receive(payload: unknown) {
        this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent);
      }

      send(payload: string) {
        this.sent.push(payload);
      }
    }

    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const emotions: unknown[] = [];
    const player = new StreamingPcmPlayer("ws://localhost/tts", {
      onEmotion: (emotion) => emotions.push(emotion),
    });

    const connection = player.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    player.sendText("你好");
    player.flush();
    socket.open();
    await connection;

    expect(socket.sent.map((payload) => JSON.parse(payload))).toEqual([
      { type: "text", text: "你好" },
      { type: "flush" },
    ]);

    socket.receive({
      type: "emotion",
      text: "完成了。",
      emotion_en: "happy",
      emotion_cn: "开心",
      emoji: "🙂",
      confidence: 1,
    });
    expect(emotions).toEqual([
      {
        text: "完成了。",
        emotion_en: "happy",
        emotion_cn: "开心",
        emoji: "🙂",
        confidence: 1,
      },
    ]);
  });

  it("prebuffers PCM before starting playback", async () => {
    const starts: number[] = [];

    class FakeAudioContext {
      currentTime = 0;
      destination = {};
      state = "running";

      resume = vi.fn(async () => undefined);

      createBuffer(_channels: number, length: number, sampleRate: number) {
        const channel = new Float32Array(length);
        return {
          duration: length / sampleRate,
          getChannelData: () => channel,
        } as unknown as AudioBuffer;
      }

      createBufferSource() {
        return {
          buffer: undefined as AudioBuffer | undefined,
          connect: vi.fn(),
          start: vi.fn((when: number) => starts.push(when)),
        } as unknown as AudioBufferSourceNode;
      }
    }

    class FakeWebSocket {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static instances: FakeWebSocket[] = [];

      binaryType = "";
      onerror?: () => void;
      onmessage?: (event: MessageEvent) => void;
      onopen?: () => void;
      readyState = FakeWebSocket.CONNECTING;

      constructor(readonly url: string) {
        FakeWebSocket.instances.push(this);
      }

      close() {
        this.readyState = 3;
      }

      open() {
        this.readyState = FakeWebSocket.OPEN;
        this.onopen?.();
      }

      receive(payload: ArrayBuffer) {
        this.onmessage?.({ data: payload } as MessageEvent);
      }

      send() {
        return undefined;
      }
    }

    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const player = new StreamingPcmPlayer("ws://localhost/tts", { prebufferSeconds: 0.6 });

    const connection = player.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    await connection;

    socket.receive(new Int16Array(12_000).buffer);
    expect(starts).toEqual([]);

    socket.receive(new Int16Array(4_800).buffer);
    expect(starts).toEqual([0, 0.5]);
  });

  it("flushes a short buffered segment at segment end", async () => {
    const starts: number[] = [];

    class FakeAudioContext {
      currentTime = 0;
      destination = {};
      state = "running";

      resume = vi.fn(async () => undefined);

      createBuffer(_channels: number, length: number, sampleRate: number) {
        const channel = new Float32Array(length);
        return {
          duration: length / sampleRate,
          getChannelData: () => channel,
        } as unknown as AudioBuffer;
      }

      createBufferSource() {
        return {
          buffer: undefined as AudioBuffer | undefined,
          connect: vi.fn(),
          start: vi.fn((when: number) => starts.push(when)),
        } as unknown as AudioBufferSourceNode;
      }
    }

    class FakeWebSocket {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      static instances: FakeWebSocket[] = [];

      binaryType = "";
      onerror?: () => void;
      onmessage?: (event: MessageEvent) => void;
      onopen?: () => void;
      readyState = FakeWebSocket.CONNECTING;

      constructor(readonly url: string) {
        FakeWebSocket.instances.push(this);
      }

      close() {
        this.readyState = 3;
      }

      open() {
        this.readyState = FakeWebSocket.OPEN;
        this.onopen?.();
      }

      receive(payload: unknown) {
        this.onmessage?.({
          data: payload instanceof ArrayBuffer ? payload : JSON.stringify(payload),
        } as MessageEvent);
      }

      send() {
        return undefined;
      }
    }

    vi.stubGlobal("AudioContext", FakeAudioContext);
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const player = new StreamingPcmPlayer("ws://localhost/tts", { prebufferSeconds: 0.6 });

    const connection = player.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    await connection;

    socket.receive(new Int16Array(4_800).buffer);
    expect(starts).toEqual([]);

    socket.receive({ type: "segment_end" });
    expect(starts).toEqual([0]);
  });
});
