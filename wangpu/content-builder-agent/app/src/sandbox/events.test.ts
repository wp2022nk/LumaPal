import { describe, expect, it } from "vitest";
import { mergeSandboxEvent } from "./events";

describe("mergeSandboxEvent", () => {
  it("merges live stdout, stderr, and completion into one command", () => {
    let logs = mergeSandboxEvent([], {
      type: "sandbox_output",
      event: "start",
      run_id: "run-1",
      command: "python demo.py",
    }, 10);
    logs = mergeSandboxEvent(logs, {
      type: "sandbox_output",
      event: "chunk",
      run_id: "run-1",
      stream: "stdout",
      chunk: "hello",
    }, 20);
    logs = mergeSandboxEvent(logs, {
      type: "sandbox_output",
      event: "chunk",
      run_id: "run-1",
      stream: "stderr",
      chunk: "warning",
    }, 30);
    logs = mergeSandboxEvent(logs, {
      type: "sandbox_output",
      event: "end",
      run_id: "run-1",
      exit_code: 0,
    }, 40);

    expect(logs).toEqual([{
      id: "run-1",
      command: "python demo.py",
      stdout: "hello",
      stderr: "warning",
      exitCode: 0,
      startedAt: 10,
      finishedAt: 40,
      status: "completed",
    }]);
  });
});
