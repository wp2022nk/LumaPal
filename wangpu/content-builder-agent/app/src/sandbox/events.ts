export interface SandboxLog {
  id: string;
  command: string;
  stdout: string;
  stderr: string;
  exitCode?: number;
  startedAt: number;
  finishedAt?: number;
  status: "running" | "completed" | "failed";
}

interface SandboxEvent {
  type: "sandbox_output";
  event: "start" | "chunk" | "end" | "error";
  run_id: string;
  command?: string;
  chunk?: string;
  stream?: "stdout" | "stderr";
  exit_code?: number;
}

export function unwrapSandboxEvent(event: unknown): SandboxEvent | null {
  if (!event || typeof event !== "object") {
    return null;
  }
  const record = event as Record<string, unknown>;
  const value = record.data && typeof record.data === "object" ? record.data : record;
  const payload = value as Partial<SandboxEvent>;
  if (payload.type !== "sandbox_output" || typeof payload.run_id !== "string") {
    return null;
  }
  return payload as SandboxEvent;
}

export function mergeSandboxEvent(
  logs: SandboxLog[],
  event: unknown,
  now = Date.now(),
): SandboxLog[] {
  const payload = unwrapSandboxEvent(event);
  if (!payload) {
    return logs;
  }

  const existing = logs.find((item) => item.id === payload.run_id);
  const next: SandboxLog = existing
    ? { ...existing }
    : {
        id: payload.run_id,
        command: payload.command || "",
        stdout: "",
        stderr: "",
        startedAt: now,
        status: "running",
      };

  if (payload.command) {
    next.command = payload.command;
  }
  if (payload.chunk) {
    if (payload.stream === "stderr") {
      next.stderr += payload.chunk;
    } else {
      next.stdout += payload.chunk;
    }
  }
  if (payload.event === "end" || payload.event === "error") {
    next.exitCode = payload.exit_code;
    next.finishedAt = now;
    next.status = payload.event === "end" && payload.exit_code === 0 ? "completed" : "failed";
  }
  return [next, ...logs.filter((item) => item.id !== payload.run_id)];
}
