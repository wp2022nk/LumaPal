import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
});
