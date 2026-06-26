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

function mockReadyFetch(extra?: (input: RequestInfo | URL) => Response | undefined) {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const custom = extra?.(input);
    if (custom) {
      return Promise.resolve(custom);
    }
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
    if (url.includes("/artifacts")) {
      return Promise.resolve(new Response(JSON.stringify({ entries: [] }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }));
    }
    if (url.includes("/state")) {
      return Promise.resolve(new Response(JSON.stringify({
        thread_id: "xiaozhi-device-a-123",
        chat: [],
        artifacts: [],
        recent_events: [],
        todos: [],
        subagents: [],
      }), { status: 200, headers: { "content-type": "application/json" } }));
    }
    return Promise.resolve(new Response(JSON.stringify({}), { status: 200, headers: { "content-type": "application/json" } }));
  });
}

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

    expect(screen.getByRole("heading", { name: "连接童芯智造工作台" })).toBeInTheDocument();
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

    const draft = await screen.findByPlaceholderText("描述你想创作的内容，或提出问题...");
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

  it("deduplicates Xiaozhi replies with different ids", async () => {
    localStorage.setItem("content-builder.thread-id", "xiaozhi-device-a-123");
    sessionStorage.setItem("content-builder.pairing-token", "phone-token");
    (globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource = MockEventSource;
    mockReadyFetch();
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

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    await act(async () => {
      MockEventSource.instances[0].emit("message_appended", {
        thread_id: "xiaozhi-device-a-123",
        source: "xiaozhi",
        message: { id: "reply-a", type: "ai", content: "这是同一条后端回复" },
      });
      MockEventSource.instances[0].emit("message_appended", {
        thread_id: "xiaozhi-device-a-123",
        source: "xiaozhi",
        message: { id: "reply-b", type: "ai", content: "这是同一条后端回复" },
      });
    });

    expect(await screen.findByText("这是同一条后端回复")).toBeInTheDocument();
    expect(screen.getAllByText("这是同一条后端回复")).toHaveLength(1);
  });

  it("replaces a shorter streamed Xiaozhi reply with the final reply", async () => {
    localStorage.setItem("content-builder.thread-id", "xiaozhi-device-a-123");
    sessionStorage.setItem("content-builder.pairing-token", "phone-token");
    (globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource = MockEventSource;
    mockReadyFetch();
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

    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    await act(async () => {
      MockEventSource.instances[0].emit("message_appended", {
        thread_id: "xiaozhi-device-a-123",
        source: "xiaozhi",
        message: { id: "reply-short", type: "ai", content: "这是短回复" },
      });
    });
    expect(await screen.findByText("这是短回复")).toBeInTheDocument();

    await act(async () => {
      MockEventSource.instances[0].emit("message_appended", {
        thread_id: "xiaozhi-device-a-123",
        source: "xiaozhi",
        message: { id: "reply-final", type: "ai", content: "这是短回复，最终完整回复" },
      });
    });

    expect(await screen.findByText("这是短回复，最终完整回复")).toBeInTheDocument();
    expect(screen.queryByText("这是短回复")).not.toBeInTheDocument();
  });

  it("deduplicates a streamed LangGraph final message replayed over app events", async () => {
    localStorage.setItem("content-builder.thread-id", "11111111-1111-4111-8111-111111111111");
    sessionStorage.setItem("content-builder.pairing-token", "phone-token");
    (globalThis as unknown as { EventSource: typeof MockEventSource }).EventSource = MockEventSource;
    mockReadyFetch();
    vi.mocked(useStream).mockReturnValue({
      client: { threads: { search: vi.fn().mockResolvedValue([]) } },
      getSubagentsByMessage: () => [],
      isLoading: false,
      messages: [{ id: "stream-final", type: "ai", content: "普通线程最终回复" }],
      stop: vi.fn(),
      subagents: new Map(),
      submit: vi.fn(),
      switchThread: vi.fn(),
      values: {},
    } as never);

    render(<App />);

    expect(await screen.findByText("普通线程最终回复")).toBeInTheDocument();
    await waitFor(() => expect(MockEventSource.instances).toHaveLength(1));
    await act(async () => {
      MockEventSource.instances[0].emit("message_appended", {
        thread_id: "11111111-1111-4111-8111-111111111111",
        source: "agent_stream",
        message: { id: "event-final", role: "agent", content: "普通线程最终回复" },
      });
    });

    expect(screen.getAllByText("普通线程最终回复")).toHaveLength(1);
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
            {
              name: "小伙伴.pdf",
              title: "小伙伴",
              path: "history/2026-06-13/conversations/thread/artifacts/storybooks/little-companions/小伙伴.pdf",
              date: "2026-06-13",
              source: "history",
              category: "storybook",
              size: 12000,
              modified_at: 1,
              mime_type: "application/pdf",
              kind: "pdf",
              preview_url: "/api/content-builder/history-preview/token/history/2026-06-13/conversations/thread/artifacts/storybooks/little-companions/%E5%B0%8F%E4%BC%99%E4%BC%B4.pdf",
            },
            {
              name: "index.html",
              title: "小星的成长成就星图",
              path: "roadshow-final-products/child-growth-achievement/index.html",
              date: "2026-06-05",
              source: "roadshow",
              category: "child_growth_achievement",
              size: 30,
              modified_at: 3,
              mime_type: "text/html",
              kind: "html",
              preview_url: "/api/content-builder/history-preview/token/roadshow-final-products/child-growth-achievement/index.html",
              cover_url: "/api/content-builder/history-preview/token/roadshow-final-products/child-growth-achievement/cover.png",
            },
            {
              name: "36a38f54b2514b919.png",
              title: "36a38f54b2514b919",
              path: "history/2026-06-15/uploads/images/36a38f54b2514b919.png",
              date: "2026-06-15",
              source: "history",
              category: "image",
              size: 23000,
              modified_at: 2,
              mime_type: "image/png",
              kind: "image",
              preview_url: "/api/content-builder/history-preview/token/history/2026-06-15/uploads/images/36a38f54b2514b919.png",
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
    expect(screen.getByAltText("小水滴滴滴的云朵大冒险 封面")).toHaveAttribute(
      "src",
      "http://127.0.0.1:2024/api/content-builder/history-preview/token/roadshow-final-products/storybook/images/page-00-cover.png",
    );
    expect(screen.getByAltText("小伙伴 封面")).toHaveAttribute(
      "src",
      "http://127.0.0.1:2024/api/content-builder/history-preview/token/history/2026-06-13/conversations/thread/artifacts/storybooks/little-companions/images/page-00-cover.png",
    );
    expect(screen.getByAltText("小星的成长成就星图 封面")).toHaveAttribute(
      "src",
      "http://127.0.0.1:2024/api/content-builder/history-preview/token/roadshow-final-products/child-growth-achievement/cover.png",
    );
    const achievementFilter = screen.getAllByRole("button", { name: /成长成就/ })[0];
    expect(achievementFilter).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "历史图片" })).toHaveAttribute(
      "src",
      "http://127.0.0.1:2024/api/content-builder/history-preview/token/history/2026-06-15/uploads/images/36a38f54b2514b919.png",
    );
    expect(screen.queryByText("36a38f54b2514b919")).not.toBeInTheDocument();
    expect(screen.queryByText("roadshow-final-products/storybook/book.html")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("开始日期"), { target: { value: "2026-06-01" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("start_date=2026-06-01"),
      expect.anything(),
    ));

    fireEvent.click(screen.getByRole("button", { name: "打开图片预览" }));
    expect(screen.getByRole("dialog", { name: "预览 图片预览" })).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("关闭"));

    fireEvent.click(screen.getByText("小水滴滴滴的云朵大冒险"));
    expect(screen.getByRole("dialog", { name: "预览 小水滴滴滴的云朵大冒险" })).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("关闭"));

    fireEvent.click(achievementFilter);
    expect(screen.getByText("小星的成长成就星图")).toBeInTheDocument();
    expect(screen.queryByText("小水滴滴滴的云朵大冒险")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("小星的成长成就星图"));
    expect(screen.getByRole("dialog", { name: "预览 小星的成长成就星图" })).toBeInTheDocument();
  });
});
