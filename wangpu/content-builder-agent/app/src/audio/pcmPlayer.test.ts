import { describe, expect, it, vi } from "vitest";
import { StreamingPcmPlayer } from "./pcmPlayer";

describe("StreamingPcmPlayer", () => {
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
});
