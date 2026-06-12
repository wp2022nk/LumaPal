import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { useStream } from "@langchain/langgraph-sdk/react";

const voiceMocks = vi.hoisted(() => ({
  close: vi.fn(),
  connect: vi.fn().mockResolvedValue(undefined),
  flush: vi.fn(),
  sendText: vi.fn(),
  unlockAudio: vi.fn().mockResolvedValue(undefined),
}));

class MockEventSource {
  static instances: MockEventSource[] = [];

  readonly listeners = new Map<string, EventListener[]>();
  readonly url: string;
  readonly close = vi.fn();

  constructor(url: string | URL) {
    this.url = String(url);
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener): void {
    this.listeners.set(type, [...(this.listeners.get(type) || []), listener]);
  }

  removeEventListener(type: string, listener: EventListener): void {
    this.listeners.set(type, (this.listeners.get(type) || []).filter((item) => item !== listener));
  }

  emit(type: string, payload: unknown): void {
    const event = new MessageEvent(type, { data: JSON.stringify(payload) });
    for (const listener of this.listeners.get(type) || []) {
      listener(event);
    }
  }
}

vi.mock("@langchain/langgraph-sdk/react", () => ({
  useStream: vi.fn(),
}));

vi.mock("./audio/pcmPlayer", () => ({
  StreamingPcmPlayer: class {
    static unlockAudio = voiceMocks.unlockAudio;

    close = voiceMocks.close;
    connect = voiceMocks.connect;
    flush = voiceMocks.flush;
    sendText = voiceMocks.sendText;
  },
}));

describe("App pairing gate", () => {
  beforeEach(() => {
    cleanup();
    localStorage.clear();
    sessionStorage.clear();
    MockEventSource.instances = [];
    (globalThis as unknown as { EventSource?: unknown }).EventSource = undefined;
    vi.clearAllMocks();
  });

  it("does not mount the protected stream client before a token is provided", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "连接电脑端 Agent Server" })).toBeInTheDocument();
    expect(useStream).not.toHaveBeenCalled();
  });

  it("keeps a stale token on the pairing page without loading threads", async () => {
    sessionStorage.setItem("content-builder.pairing-token", "stale-token");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      paired: false,
    }), { status: 200, headers: { "content-type": "application/json" } }));

    render(<App />);

    await waitFor(() => expect(screen.getByText("连接尚未就绪。请确认电脑端服务已启动，并填写当前显示的配对 Token。")).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(useStream).not.toHaveBeenCalled();
  });

  it("keeps the conversation drawer closed on phones until its own toggle is pressed", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    sessionStorage.setItem("content-builder.pairing-token", "phone-token");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      paired: true,
      agent_server_ready: true,
    }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.mocked(useStream).mockReturnValue({
      client: { threads: { search: vi.fn().mockResolvedValue([]) } },
      getSubagentsByMessage: () => [],
      isLoading: false,
      messages: [],
      stop: vi.fn(),
      subagents: new Map(),
      submit: vi.fn(),
      switchThread: vi.fn(),
      values: {},
    } as never);

    const { container } = render(<App />);

    await waitFor(() => expect(screen.getByLabelText("切换会话栏")).toBeInTheDocument());
    const sidebar = container.querySelector(".session-sidebar");
    expect(sidebar).toHaveClass("closed");
    fireEvent.click(screen.getByLabelText("切换会话栏"));
    expect(sidebar).toHaveClass("open");
    expect(screen.getByLabelText("关闭会话栏")).toBeInTheDocument();
  });

  it("opens the TTS channel before submitting a typed message", async () => {
    sessionStorage.setItem("content-builder.pairing-token", "phone-token");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      paired: true,
      agent_server_ready: true,
    }), { status: 200, headers: { "content-type": "application/json" } }));
    const submit = vi.fn().mockResolvedValue(undefined);
    vi.mocked(useStream).mockReturnValue({
      client: {
        threads: {
          create: vi.fn().mockResolvedValue({ thread_id: "typed-thread", metadata: {}, updated_at: "" }),
          search: vi.fn().mockResolvedValue([]),
        },
      },
      getSubagentsByMessage: () => [],
      isLoading: false,
      messages: [],
      stop: vi.fn(),
      subagents: new Map(),
      submit,
      switchThread: vi.fn(),
      values: {},
    } as never);

    render(<App />);

    const draft = await screen.findByPlaceholderText("描述你想创作的内容...");
    fireEvent.change(draft, { target: { value: "你好" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));

    await waitFor(() => expect(submit).toHaveBeenCalledOnce());
    expect(voiceMocks.unlockAudio).toHaveBeenCalled();
    expect(voiceMocks.connect).toHaveBeenCalled();
    expect(voiceMocks.connect.mock.invocationCallOrder[0]).toBeLessThan(submit.mock.invocationCallOrder[0]);
  });

  it("keeps Xiaozhi hardware sessions out of LangGraph thread switching", async () => {
    sessionStorage.setItem("content-builder.pairing-token", "phone-token");
    (globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource = MockEventSource;
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/gateway/status")) {
        return Promise.resolve(new Response(JSON.stringify({
          paired: true,
          agent_server_ready: true,
        }), { status: 200, headers: { "content-type": "application/json" } }));
      }
      if (url.includes("/history/artifacts")) {
        return Promise.resolve(new Response(JSON.stringify({ entries: [] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }));
      }
      return Promise.resolve(new Response(JSON.stringify({}), { status: 200, headers: { "content-type": "application/json" } }));
    });
    const switchThread = vi.fn();
    vi.mocked(useStream).mockReturnValue({
      client: { threads: { search: vi.fn().mockResolvedValue([]) } },
      getSubagentsByMessage: () => [],
      isLoading: false,
      messages: [{ id: "agent-message", type: "ai", content: "agent text" }],
      stop: vi.fn(),
      subagents: new Map(),
      submit: vi.fn(),
      switchThread,
      values: {},
    } as never);

    render(<App />);

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    await act(async () => {
      MockEventSource.instances[0].emit("thread_created", {
        thread_id: "xiaozhi-device-a-123",
        device_id: "device-a",
        updated_at: "2026-06-12T08:00:00Z",
      });
    });
    fireEvent.click(await screen.findByText("Xiaozhi device-a"));

    expect(switchThread).toHaveBeenCalledWith(null);
    expect(switchThread).not.toHaveBeenCalledWith("xiaozhi-device-a-123");
    expect(screen.queryByText("agent text")).not.toBeInTheDocument();
  });

  it("shows historical artifacts with date filters and opens a preview", async () => {
    sessionStorage.setItem("content-builder.pairing-token", "phone-token");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      const url = String(input);
      if (url.includes("/gateway/status")) {
        return Promise.resolve(new Response(JSON.stringify({
          paired: true,
          agent_server_ready: true,
        }), { status: 200, headers: { "content-type": "application/json" } }));
      }
      if (url.includes("/history/artifacts")) {
        return Promise.resolve(new Response(JSON.stringify({
          entries: [
            {
              name: "book.html",
              title: "小水滴滴滴的云朵大冒险",
              path: "roadshow-final-products/storybook/book.html",
              date: "2026-06-05",
              source: "roadshow",
              category: "audiobook",
              size: 10,
              modified_at: 1,
              mime_type: "text/html",
              kind: "html",
              preview_url: "/api/content-builder/history-preview/token/roadshow-final-products/storybook/book.html",
            },
          ],
        }), { status: 200, headers: { "content-type": "application/json" } }));
      }
      return Promise.resolve(new Response(JSON.stringify({}), { status: 200, headers: { "content-type": "application/json" } }));
    });
    vi.mocked(useStream).mockReturnValue({
      client: { threads: { search: vi.fn().mockResolvedValue([]) } },
      getSubagentsByMessage: () => [],
      isLoading: false,
      messages: [],
      stop: vi.fn(),
      subagents: new Map(),
      submit: vi.fn(),
      switchThread: vi.fn(),
      values: {},
    } as never);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /历史/ }));
    expect(await screen.findByText("历史产物档案馆")).toBeInTheDocument();
    expect(await screen.findByText("小水滴滴滴的云朵大冒险")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("开始日期"), { target: { value: "2026-06-01" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("start_date=2026-06-01"),
      expect.anything(),
    ));

    fireEvent.click(screen.getByText("小水滴滴滴的云朵大冒险"));
    expect(screen.getByRole("dialog", { name: "预览 book.html" })).toBeInTheDocument();
  });
});
