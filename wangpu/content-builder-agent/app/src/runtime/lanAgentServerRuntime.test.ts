import { afterEach, describe, expect, it, vi } from "vitest";
import { LanAgentServerRuntime, normalizeBaseUrl } from "./lanAgentServerRuntime";

describe("LanAgentServerRuntime", () => {
  afterEach(() => vi.restoreAllMocks());

  it("normalizes the configured server address", () => {
    expect(normalizeBaseUrl(" http://192.168.1.7:2024/// ")).toBe("http://192.168.1.7:2024");
  });

  it("adds the pairing token without exposing model keys", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      configured: { qwen: true, dashscope: false, tavily: false },
    }), { status: 200, headers: { "content-type": "application/json" } }));
    const runtime = new LanAgentServerRuntime({ baseUrl: "http://localhost:2024/", pairingToken: "pair-me" });

    await expect(runtime.getKeyStatus()).resolves.toEqual({
      configured: { qwen: true, dashscope: false, tavily: false },
    });
    expect(fetchMock).toHaveBeenCalledOnce();
    const [, options] = fetchMock.mock.calls[0];
    expect(new Headers(options?.headers).get("x-api-key")).toBe("pair-me");
  });

  it("checks pairing before the protected stream client mounts", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      paired: false,
      agent_server_ready: false,
    }), { status: 200, headers: { "content-type": "application/json" } }));
    const runtime = new LanAgentServerRuntime({ baseUrl: "http://localhost:2024/", pairingToken: "stale-token" });

    await expect(runtime.verifyPairing()).resolves.toBe(false);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:2024/api/content-builder/gateway/status",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  it("mounts streaming only after pairing and the Agent Server are ready", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      paired: true,
      agent_server_ready: true,
    }), { status: 200, headers: { "content-type": "application/json" } }));
    const runtime = new LanAgentServerRuntime({ baseUrl: "http://localhost:2024/", pairingToken: "pair-me" });

    await expect(runtime.verifyPairing()).resolves.toBe(true);
  });

  it("uses a tokenized WebSocket URL for TTS", () => {
    const runtime = new LanAgentServerRuntime({ baseUrl: "https://example.test", pairingToken: "a token" });
    expect(runtime.ttsSocketUrl("thread-one")).toBe(
      "wss://example.test/api/content-builder/threads/thread-one/voice/tts?token=a%20token",
    );
  });
});
